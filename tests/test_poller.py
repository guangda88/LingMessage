from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lingmessage.mailbox import Mailbox
from lingmessage.poller import DiscussionPoller, PollerState
from lingmessage.types import (
    Channel,
    LingIdentity,
)


def _make_thread(mb: Mailbox, participants: tuple[LingIdentity, ...], topic: str = "test") -> str:
    sender = participants[0]
    header, _ = mb.open_thread(
        sender=sender,
        recipients=participants[1:],
        channel=Channel.ECOSYSTEM,
        topic=topic,
        subject="test subject",
        body="test body",
    )
    return header.thread_id


class TestPollerState:
    def test_initial_state_empty(self, tmp_path: Path) -> None:
        state = PollerState(path=tmp_path / "state.json")
        assert state.get_reminder_level("t1", "lingflow") == 0

    def test_record_and_get_level(self, tmp_path: Path) -> None:
        state = PollerState(path=tmp_path / "state.json")
        state.record_reminder("t1", "lingflow", 2)
        assert state.get_reminder_level("t1", "lingflow") == 2

    def test_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        state1 = PollerState(path=path)
        state1.record_reminder("t1", "lingclaude", 1)
        state2 = PollerState(path=path)
        assert state2.get_reminder_level("t1", "lingclaude") == 1

    def test_cleanup_thread(self, tmp_path: Path) -> None:
        state = PollerState(path=tmp_path / "state.json")
        state.record_reminder("t1", "lingflow", 1)
        state.record_reminder("t1", "lingclaude", 2)
        state.cleanup_thread("t1")
        assert state.get_reminder_level("t1", "lingflow") == 0
        assert state.get_reminder_level("t1", "lingclaude") == 0

    def test_corrupted_file_no_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("NOT JSON{{{{")
        state = PollerState(path=path)
        assert state.get_reminder_level("t1", "lingflow") == 0

    def test_record_reminder_updates_level(self, tmp_path: Path) -> None:
        state = PollerState(path=tmp_path / "state.json")
        state.record_reminder("t1", "lingyi", 1)
        assert state.get_reminder_level("t1", "lingyi") == 1
        state.record_reminder("t1", "lingyi", 3)
        assert state.get_reminder_level("t1", "lingyi") == 3


class TestDiscussionPollerScan:
    def test_scan_empty_mailbox(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        poller = DiscussionPoller(mailbox=mb)
        result = poller.scan_once()
        assert result["scanned"] == 0
        assert result["actions"] == []

    def test_scan_all_replied_no_action(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        header, _ = mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="all replied",
            subject="s",
            body="b",
        )
        mb.reply(
            thread_id=header.thread_id,
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGFLOW,
            subject="re",
            body="reply",
        )
        poller = DiscussionPoller(mailbox=mb)
        result = poller.scan_once()
        assert len(result["actions"]) == 0

    @patch("lingmessage.poller.DiscussionPoller._notify_endpoint", return_value=False)
    def test_scan_detects_waiting_participant(self, mock_notify, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGYI),
            channel=Channel.ECOSYSTEM,
            topic="waiting",
            subject="s",
            body="b",
        )
        poller = DiscussionPoller(
            mailbox=mb,
            first_hours=0,
            second_hours=999,
            escalate_hours=9999,
        )
        result = poller.scan_once()
        assert len(result["actions"]) > 0
        assert any("首次提醒" in a for a in result["actions"])

    @patch("lingmessage.poller.DiscussionPoller._notify_endpoint", return_value=False)
    def test_scan_escalation(self, mock_notify, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="escalate",
            subject="s",
            body="b",
        )
        poller = DiscussionPoller(
            mailbox=mb,
            first_hours=0,
            second_hours=0,
            escalate_hours=0,
        )
        result = poller.scan_once()
        assert any("升级通知" in a for a in result["actions"])

    def test_init_existing_marks_all(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        _make_thread(mb, (LingIdentity.LINGFLOW, LingIdentity.LINGCLAUDE), "t1")
        _make_thread(mb, (LingIdentity.LINGYI, LingIdentity.LINGZHI), "t2")
        poller = DiscussionPoller(mailbox=mb)
        poller.init_existing()
        assert poller._stats.get("init_marked", 0) > 0

    @patch("lingmessage.poller.DiscussionPoller._notify_endpoint", return_value=False)
    def test_scan_no_repeat_reminder(self, mock_notify, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="no repeat",
            subject="s",
            body="b",
        )
        state = PollerState(path=tmp_path / "state.json")
        poller = DiscussionPoller(
            mailbox=mb,
            state=state,
            first_hours=0,
            second_hours=999,
            escalate_hours=9999,
        )
        result1 = poller.scan_once()
        assert len(result1["actions"]) == 1
        result2 = poller.scan_once()
        assert len(result2["actions"]) == 0


class TestPollerParseTime:
    def test_valid_iso(self) -> None:
        dt = DiscussionPoller._parse_time("2026-04-09T01:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_naive_gets_utc(self) -> None:
        dt = DiscussionPoller._parse_time("2026-04-09T01:00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_invalid_returns_none(self) -> None:
        assert DiscussionPoller._parse_time("not-a-date") is None

    def test_empty_returns_none(self) -> None:
        assert DiscussionPoller._parse_time("") is None
