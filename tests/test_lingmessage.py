from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from lingmessage.mailbox import Mailbox
from lingmessage.seed import seed_all
from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    ThreadHeader,
    ThreadStatus,
    create_message,
    create_thread_header,
)


@pytest.fixture
def tmp_mailbox(tmp_path: Path) -> Mailbox:
    return Mailbox(root=tmp_path / ".lingmessage")


class TestTypes:
    def test_message_frozen(self) -> None:
        msg = create_message(
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGFLOW,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="hello",
        )
        with pytest.raises(AttributeError):
            msg.body = "changed"  # type: ignore[misc]

    def test_message_to_dict_roundtrip(self) -> None:
        msg = create_message(
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGYI,
            message_type=MessageType.REPLY,
            channel=Channel.KNOWLEDGE,
            subject="re: test",
            body="world",
            metadata={"key": "val"},
        )
        d = msg.to_dict()
        restored = Message.from_dict(d)
        assert restored.message_id == msg.message_id
        assert restored.sender == msg.sender
        assert restored.channel == msg.channel
        assert dict(restored.metadata) == {"key": "val"}

    def test_message_to_json(self) -> None:
        msg = create_message(
            sender=LingIdentity.LINGYI,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="json test",
            body="body",
        )
        j = msg.to_json(indent=2)
        data = json.loads(j)
        assert data["sender"] == "lingyi"

    def test_thread_header_frozen(self) -> None:
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.INTEGRATION,
            subject="thread test",
            body="body",
        )
        header = create_thread_header(
            topic="test topic",
            channel=Channel.INTEGRATION,
            participants=(LingIdentity.LINGFLOW,),
            first_message=msg,
        )
        with pytest.raises(AttributeError):
            header.topic = "changed"  # type: ignore[misc]

    def test_thread_header_roundtrip(self) -> None:
        msg = create_message(
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.SELF_OPTIMIZE,
            subject="roundtrip",
            body="body",
        )
        header = create_thread_header(
            topic="roundtrip topic",
            channel=Channel.SELF_OPTIMIZE,
            participants=(LingIdentity.LINGCLAUDE, LingIdentity.LINGMINOPT),
            first_message=msg,
        )
        d = header.to_dict()
        restored = ThreadHeader.from_dict(d)
        assert restored.thread_id == header.thread_id
        assert restored.topic == header.topic
        assert restored.message_count == 1

    def test_all_ling_identities(self) -> None:
        assert len(LingIdentity) == 9

    def test_all_message_types(self) -> None:
        assert len(MessageType) == 8

    def test_all_channels(self) -> None:
        assert len(Channel) == 6


class TestMailbox:
    def test_post_message(self, tmp_mailbox: Mailbox) -> None:
        msg = create_message(
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGFLOW,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="hello",
        )
        tmp_mailbox.post(msg)
        messages = tmp_mailbox.load_thread_messages(msg.thread_id)
        assert len(messages) == 1
        assert messages[0].body == "hello"

    def test_open_thread(self, tmp_mailbox: Mailbox) -> None:
        header, first = tmp_mailbox.open_thread(
            sender=LingIdentity.LINGYI,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.KNOWLEDGE,
            topic="test topic",
            subject="opening",
            body="first message",
        )
        assert header.topic == "test topic"
        assert header.message_count == 1
        loaded = tmp_mailbox.load_thread_header(header.thread_id)
        assert loaded is not None
        assert loaded.thread_id == header.thread_id

    def test_reply(self, tmp_mailbox: Mailbox) -> None:
        header, _ = tmp_mailbox.open_thread(
            sender=LingIdentity.LINGCLAUDE,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.INTEGRATION,
            topic="reply test",
            subject="q",
            body="question",
        )
        reply = tmp_mailbox.reply(
            thread_id=header.thread_id,
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.LINGCLAUDE,
            subject="re: q",
            body="answer",
        )
        assert reply.thread_id == header.thread_id
        messages = tmp_mailbox.load_thread_messages(header.thread_id)
        assert len(messages) == 2

    def test_reply_to_nonexistent_thread_raises(self, tmp_mailbox: Mailbox) -> None:
        with pytest.raises(ValueError, match="not found"):
            tmp_mailbox.reply(
                thread_id="nonexistent",
                sender=LingIdentity.LINGCLAUDE,
                recipient=LingIdentity.LINGFLOW,
                subject="x",
                body="y",
            )

    def test_list_threads(self, tmp_mailbox: Mailbox) -> None:
        tmp_mailbox.open_thread(
            sender=LingIdentity.LINGCLAUDE,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.ECOSYSTEM,
            topic="eco topic",
            subject="s1",
            body="b1",
        )
        tmp_mailbox.open_thread(
            sender=LingIdentity.LINGYI,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.KNOWLEDGE,
            topic="know topic",
            subject="s2",
            body="b2",
        )
        all_threads = tmp_mailbox.list_threads()
        assert len(all_threads) == 2
        eco_threads = tmp_mailbox.list_threads(channel=Channel.ECOSYSTEM)
        assert len(eco_threads) == 1
        know_threads = tmp_mailbox.list_threads(channel=Channel.KNOWLEDGE)
        assert len(know_threads) == 1

    def test_list_threads_by_participant(self, tmp_mailbox: Mailbox) -> None:
        tmp_mailbox.open_thread(
            sender=LingIdentity.LINGCLAUDE,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.ECOSYSTEM,
            topic="p test",
            subject="s",
            body="b",
        )
        results = tmp_mailbox.list_threads(participant=LingIdentity.LINGCLAUDE)
        assert len(results) == 1
        results2 = tmp_mailbox.list_threads(participant=LingIdentity.LINGZHI)
        assert len(results2) == 0

    def test_load_nonexistent_thread(self, tmp_mailbox: Mailbox) -> None:
        assert tmp_mailbox.load_thread_header("nope") is None
        assert tmp_mailbox.load_thread_messages("nope") == ()

    def test_get_summary(self, tmp_mailbox: Mailbox) -> None:
        tmp_mailbox.open_thread(
            sender=LingIdentity.LINGCLAUDE,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.ECOSYSTEM,
            topic="summary test",
            subject="s",
            body="b",
        )
        summary = tmp_mailbox.get_summary()
        assert summary["total_threads"] == 1
        assert summary["total_messages"] >= 1
        assert "ecosystem" in summary["by_channel"]


class TestSeed:
    def test_seed_creates_threads(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / ".lingmessage")
        threads = seed_all(mailbox)
        assert len(threads) == 6
        for name, tid in threads.items():
            assert tid, f"Thread {name} has no ID"
            header = mailbox.load_thread_header(tid)
            assert header is not None, f"Thread {name} header not found"

    def test_seed_creates_messages(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / ".lingmessage")
        threads = seed_all(mailbox)
        total = 0
        for tid in threads.values():
            msgs = mailbox.load_thread_messages(tid)
            total += len(msgs)
            assert len(msgs) >= 3, f"Thread {tid} has too few messages"
        assert total >= 20, f"Only {total} seed messages"

    def test_seed_index_valid(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / ".lingmessage")
        seed_all(mailbox)
        summary = mailbox.get_summary()
        assert summary["total_threads"] == 6

    def test_seed_thread_topics(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / ".lingmessage")
        threads = seed_all(mailbox)
        for tid in threads.values():
            header = mailbox.load_thread_header(tid)
            assert header is not None
            assert len(header.topic) > 10

    def test_seed_all_channels_used(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / ".lingmessage")
        seed_all(mailbox)
        threads = mailbox.list_threads()
        channels = {t.channel for t in threads}
        assert "ecosystem" in channels
        assert "shared-infra" in channels
        assert "self-optimize" in channels
        assert "knowledge" in channels
