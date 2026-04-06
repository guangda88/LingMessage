from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.lingbus import BusMessage, LingBus
from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus")
    yield b
    b.close()


class TestLingBusInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        b = LingBus(bus_dir=tmp_path / "bus")
        assert (tmp_path / "bus" / "lingbus.db").exists()
        b.close()

    def test_creates_dir_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "dir"
        b = LingBus(bus_dir=target)
        assert target.exists()
        b.close()

    def test_schema_tables_exist(self, bus: LingBus) -> None:
        tables = bus._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in tables]
        assert "threads" in names
        assert "messages" in names

    def test_wal_mode(self, bus: LingBus) -> None:
        mode = bus._conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"


class TestLingBusClose:
    def test_close_idempotent(self, tmp_path: Path) -> None:
        b = LingBus(bus_dir=tmp_path / "bus")
        b.close()
        with pytest.raises(Exception):
            b._conn.execute("SELECT 1")


class TestOpenThread:
    def test_returns_thread_and_message_ids(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="test topic", sender="lingflow", recipients=["lingclaude"],
        )
        assert len(tid) == 32
        assert len(mid) == 32

    def test_thread_stored_correctly(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="my topic",
            sender="lingflow",
            recipients=["lingclaude", "lingyi"],
            channel="knowledge",
            subject="hello",
            body="world",
        )
        row = bus._conn.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (tid,)
        ).fetchone()
        assert row["topic"] == "my topic"
        assert row["channel"] == "knowledge"
        assert row["status"] == "active"
        assert row["message_count"] == 1

    def test_first_message_stored(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
            subject="sub", body="body text",
        )
        row = bus._conn.execute(
            "SELECT * FROM messages WHERE message_id = ?", (mid,)
        ).fetchone()
        assert row["thread_id"] == tid
        assert row["sender"] == "lingflow"
        assert row["body"] == "body text"
        assert row["message_type"] == "open"

    def test_participants_deduplicated(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingflow", "lingclaude"],
        )
        row = bus._conn.execute(
            "SELECT participants FROM threads WHERE thread_id = ?", (tid,)
        ).fetchone()
        import json
        parts = json.loads(row["participants"])
        assert len(parts) == 2


class TestPostReply:
    def test_reply_stored(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        mid = bus.post_reply(
            thread_id=tid, sender="lingclaude", recipient="lingflow", body="reply body",
        )
        assert len(mid) == 32

    def test_reply_message_count_incremented(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingclaude", "lingflow", "r1")
        bus.post_reply(tid, "lingflow", "lingclaude", "r2")
        row = bus._conn.execute(
            "SELECT message_count FROM threads WHERE thread_id = ?", (tid,)
        ).fetchone()
        assert row["message_count"] == 3

    def test_reply_to_nonexistent_thread_raises(self, bus: LingBus) -> None:
        with pytest.raises(ValueError, match="not found"):
            bus.post_reply("nonexistent", "lingflow", "lingclaude", "body")

    def test_reply_new_sender_added_to_participants(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingyi", "lingflow", "chiming in")
        import json
        row = bus._conn.execute(
            "SELECT participants FROM threads WHERE thread_id = ?", (tid,)
        ).fetchone()
        parts = json.loads(row["participants"])
        assert "lingyi" in parts


class TestPoll:
    def test_poll_returns_matching_messages(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingclaude", "lingflow", "reply")
        msgs = bus.poll("lingflow", since_rowid=0)
        assert len(msgs) >= 2

    def test_poll_respects_since_rowid(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        msgs_all = bus.poll("lingclaude", since_rowid=0)
        max_rid = max(m.rowid for m in msgs_all)
        msgs_after = bus.poll("lingclaude", since_rowid=max_rid)
        assert len(msgs_after) == 0

    def test_poll_returns_bus_message_objects(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        msgs = bus.poll("lingclaude", since_rowid=0)
        assert len(msgs) == 1
        assert isinstance(msgs[0], BusMessage)
        assert msgs[0].sender == "lingflow"


class TestGetThread:
    def test_returns_all_thread_messages(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingclaude", "lingflow", "r1")
        bus.post_reply(tid, "lingflow", "lingclaude", "r2")
        msgs = bus.get_thread(tid)
        assert len(msgs) == 3

    def test_empty_thread(self, bus: LingBus) -> None:
        msgs = bus.get_thread("nonexistent")
        assert msgs == []


class TestListThreads:
    def test_lists_all_threads(self, bus: LingBus) -> None:
        bus.open_thread(topic="t1", sender="lingflow", recipients=["lingclaude"])
        bus.open_thread(topic="t2", sender="lingyi", recipients=["lingflow"])
        threads = bus.list_threads()
        assert len(threads) == 2

    def test_filter_by_status(self, bus: LingBus) -> None:
        bus.open_thread(topic="t1", sender="lingflow", recipients=["lingclaude"])
        threads = bus.list_threads(status="active")
        assert len(threads) == 1
        threads_closed = bus.list_threads(status="closed")
        assert len(threads_closed) == 0

    def test_thread_dict_fields(self, bus: LingBus) -> None:
        bus.open_thread(topic="my topic", sender="lingflow", recipients=["lingclaude"])
        t = bus.list_threads()[0]
        assert "thread_id" in t
        assert "topic" in t
        assert "channel" in t
        assert "status" in t
        assert "participants" in t
        assert "message_count" in t


class TestAck:
    def test_ack_existing_message(self, bus: LingBus) -> None:
        _, mid = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        result = bus.ack(mid, "lingclaude")
        assert result is True

    def test_ack_nonexistent_message(self, bus: LingBus) -> None:
        result = bus.ack("nonexistent", "lingflow")
        assert result is False

    def test_ack_idempotent(self, bus: LingBus) -> None:
        _, mid = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        bus.ack(mid, "lingclaude")
        import json
        row = bus._conn.execute(
            "SELECT acked_by FROM messages WHERE message_id = ?", (mid,)
        ).fetchone()
        acked = json.loads(row["acked_by"])
        assert acked.count("lingclaude") == 1


class TestGetMaxRowid:
    def test_returns_zero_when_empty(self, bus: LingBus) -> None:
        assert bus.get_max_rowid("lingflow") == 0

    def test_returns_max_after_messages(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        rid = bus.get_max_rowid("lingclaude")
        assert rid > 0

    def test_sees_all_recipient_messages(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        rid_flow = bus.get_max_rowid("lingflow")
        rid_claude = bus.get_max_rowid("lingclaude")
        assert rid_flow > 0
        assert rid_claude > 0


class TestStats:
    def test_empty_stats(self, bus: LingBus) -> None:
        s = bus.stats()
        assert s["threads"] == 0
        assert s["messages"] == 0
        assert s["unacked"] == 0

    def test_stats_after_activity(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingclaude", "lingflow", "r1")
        s = bus.stats()
        assert s["threads"] == 1
        assert s["messages"] == 2
        assert s["unacked"] == 2


class TestContextManager:
    def test_context_manager(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            bus.open_thread(topic="t", sender="lingflow", recipients=["lingclaude"])
            assert bus.stats()["threads"] == 1
        with pytest.raises(Exception):
            bus._conn.execute("SELECT 1")


class TestSyncFromMailbox:
    def test_sync_imports_threads(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="sync topic",
            subject="hello",
            body="world",
        )
        mb.reply(
            thread_id=mb.list_threads()[0].thread_id,
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGFLOW,
            subject="re",
            body="reply",
        )

        with LingBus(bus_dir=tmp_path / "bus") as bus:
            n = bus.sync_from_mailbox(mb)
            assert n == 1
            threads = bus.list_threads()
            assert len(threads) == 1
            assert threads[0]["topic"] == "sync topic"
            msgs = bus.get_thread(threads[0]["thread_id"])
            assert len(msgs) == 2

    def test_sync_idempotent(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGYI,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.KNOWLEDGE,
            topic="idem",
            subject="s",
            body="b",
        )
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            assert bus.sync_from_mailbox(mb) == 1
            assert bus.sync_from_mailbox(mb) == 0

    def test_sync_empty_mailbox(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            assert bus.sync_from_mailbox(mb) == 0
