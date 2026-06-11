from __future__ import annotations

import time
from pathlib import Path

import pytest

from lingmessage.lingbus import BusMessage, LingBus
from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
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

    def test_poll_respects_reverse_flag(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        bus.post_reply(tid, "lingclaude", "lingflow", "reply1")
        bus.post_reply(tid, "lingflow", "lingclaude", "reply2")
        bus.post_reply(tid, "lingclaude", "lingflow", "reply3")
        msgs_asc = bus.poll("lingflow", since_rowid=0, reverse=False)
        msgs_desc = bus.poll("lingflow", since_rowid=0, reverse=True)
        # In ASC order, oldest first (open message then replies)
        assert msgs_asc[0].body == ""  # Initial open message
        assert msgs_asc[1].body == "reply1"
        # In DESC order, newest first
        assert msgs_desc[0].body == "reply3"


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

    def test_returns_messages_in_reverse_order_by_default(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        r1_id = bus.post_reply(tid, "lingclaude", "lingflow", "first reply")
        r2_id = bus.post_reply(tid, "lingflow", "lingclaude", "second reply")
        r3_id = bus.post_reply(tid, "lingclaude", "lingflow", "third reply")
        msgs = bus.get_thread(tid, reverse=True)
        assert len(msgs) == 4
        assert msgs[0].message_id == r3_id  # Most recent first
        assert msgs[1].message_id == r2_id
        assert msgs[2].message_id == r1_id

    def test_returns_messages_in_chronological_order_when_reverse_false(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        r1_id = bus.post_reply(tid, "lingclaude", "lingflow", "first reply")
        r2_id = bus.post_reply(tid, "lingflow", "lingclaude", "second reply")
        msgs = bus.get_thread(tid, reverse=False)
        assert len(msgs) == 3
        assert msgs[0].message_id != r1_id  # First is the initial open message
        assert msgs[1].message_id == r1_id  # Oldest reply first
        assert msgs[2].message_id == r2_id  # Most recent reply last


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


class TestSyncToMailbox:
    def test_sync_exports_threads(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            tid, mid = bus.open_thread(
                topic="bus topic", sender="lingflow", recipients=["lingclaude"],
                channel="integration", subject="test", body="from bus",
            )
            bus.post_reply(tid, "lingclaude", "lingflow", "bus reply")

            mb = Mailbox(root=tmp_path / "mb")
            n = bus.sync_to_mailbox(mb)
            assert n == 1

            headers = mb.list_threads()
            assert len(headers) == 1
            assert headers[0].topic == "bus topic"

            msgs = mb.load_thread_messages(headers[0].thread_id)
            assert len(msgs) == 2

    def test_sync_to_mailbox_idempotent(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            bus.open_thread(
                topic="idem", sender="lingflow", recipients=["lingclaude"],
            )
            mb = Mailbox(root=tmp_path / "mb")
            assert bus.sync_to_mailbox(mb) == 1
            assert bus.sync_to_mailbox(mb) == 0

    def test_sync_to_mailbox_empty_bus(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            mb = Mailbox(root=tmp_path / "mb")
            assert bus.sync_to_mailbox(mb) == 0

    def test_sync_to_mailbox_preserves_metadata(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            tid, _ = bus.open_thread(
                topic="meta", sender="lingflow", recipients=["lingclaude"],
                channel="knowledge",
            )
            bus.post_reply(
                tid, "lingclaude", "lingflow", "with meta",
                metadata={"key1": "val1"},
            )
            mb = Mailbox(root=tmp_path / "mb")
            bus.sync_to_mailbox(mb)

            msgs = mb.load_thread_messages(mb.list_threads()[0].thread_id)
            reply_msg = [m for m in msgs if m.body == "with meta"][0]
            assert ("key1", "val1") in reply_msg.metadata

    def test_bidirectional_sync_roundtrip(self, tmp_path: Path) -> None:
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="from mailbox",
            subject="hello",
            body="world",
        )
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            bus.open_thread(
                topic="from bus", sender="lingyi", recipients=["lingzhi"],
            )
            bus.sync_from_mailbox(mb)
            assert bus.stats()["threads"] == 2

        mb2 = Mailbox(root=tmp_path / "mb2")
        with LingBus(bus_dir=tmp_path / "bus", throttle=False) as bus:
            bus.sync_to_mailbox(mb2)
            headers = mb2.list_threads()
            assert len(headers) == 2
            topics = {h.topic for h in headers}
            assert "from mailbox" in topics
            assert "from bus" in topics


class TestSenderIdentityVerification:
    def test_open_thread_rejects_unknown_sender(self, bus: LingBus) -> None:
        with pytest.raises(ValueError, match="unknown sender"):
            bus.open_thread(topic="t", sender="hacker", recipients=["lingflow"])

    def test_post_reply_rejects_unknown_sender(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        with pytest.raises(ValueError, match="unknown sender"):
            bus.post_reply(tid, "impostor", "lingflow", "bad reply")

    def test_open_thread_accepts_all_registered_senders(self, bus: LingBus) -> None:
        valid = [e.value for e in LingIdentity if e.value != "all"]
        for s in valid[:3]:
            tid, _ = bus.open_thread(topic="t", sender=s, recipients=["lingflow"])
            assert tid

    def test_post_reply_accepts_registered_sender(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
        )
        mid = bus.post_reply(tid, "lingclaude", "lingflow", "ok")
        assert mid


class TestDeliveryAttemptsTable:
    def test_table_created_on_init(self, bus: LingBus) -> None:
        tables = bus._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in tables]
        assert "delivery_attempts" in names

    def test_delivery_index_created(self, bus: LingBus) -> None:
        indexes = bus._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in indexes]
        assert "idx_delivery_status" in names


class TestConfirmDelivery:
    def test_confirmed_when_recipient_acked(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        result = bus.confirm_delivery(mid, "lingclaude")
        assert result["status"] == "confirmed"

    def test_pending_on_first_attempt_without_ack(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        result = bus.confirm_delivery(mid, "lingclaude")
        assert result["status"] == "pending"
        assert result["attempt_count"] == 1

    def test_increments_attempts(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        r1 = bus.confirm_delivery(mid, "lingclaude")
        assert r1["attempt_count"] == 1
        r2 = bus.confirm_delivery(mid, "lingclaude")
        assert r2["attempt_count"] == 2

    def test_escalates_after_max_attempts(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")  # attempt 1
        bus.confirm_delivery(mid, "lingclaude")  # attempt 2
        r3 = bus.confirm_delivery(mid, "lingclaude")  # attempt 3 -> escalated
        assert r3["status"] == "escalated"
        assert r3["attempt_count"] == 3

    def test_stays_escalated_on_further_calls(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")
        bus.confirm_delivery(mid, "lingclaude")
        bus.confirm_delivery(mid, "lingclaude")  # escalated
        r4 = bus.confirm_delivery(mid, "lingclaude")
        assert r4["status"] == "escalated"

    def test_skips_broadcast_recipient(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        result = bus.confirm_delivery(mid, "all")
        assert result["status"] == "skipped"
        assert result["reason"] == "broadcast"

    def test_skips_nonexistent_message(self, bus: LingBus) -> None:
        result = bus.confirm_delivery("nonexistent-id", "lingclaude")
        assert result["status"] == "skipped"
        assert result["reason"] == "message_not_found"

    def test_confirmed_after_late_ack(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")  # pending
        bus.ack(mid, "lingclaude")
        r2 = bus.confirm_delivery(mid, "lingclaude")
        assert r2["status"] == "confirmed"

    def test_idempotent_confirmed(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        r1 = bus.confirm_delivery(mid, "lingclaude")
        r2 = bus.confirm_delivery(mid, "lingclaude")
        assert r1["status"] == "confirmed"
        assert r2["status"] == "confirmed"


class TestGetDeliveryStatus:
    def test_empty_for_unknown_message(self, bus: LingBus) -> None:
        result = bus.get_delivery_status("nonexistent-id")
        assert result == []

    def test_returns_delivery_records(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")
        records = bus.get_delivery_status(mid)
        assert len(records) == 1
        assert records[0]["recipient"] == "lingclaude"
        assert records[0]["status"] == "pending"
        assert records[0]["attempt_count"] == 1

    def test_multiple_recipients(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow",
            recipients=["lingclaude", "lingzhi"],
        )
        bus.confirm_delivery(mid, "lingclaude")
        bus.confirm_delivery(mid, "lingzhi")
        records = bus.get_delivery_status(mid)
        assert len(records) == 2


class TestPendingDeliveries:
    def test_empty_when_no_pending(self, bus: LingBus) -> None:
        result = bus.pending_deliveries()
        assert result == []

    def test_returns_pending_with_first_attempt(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")
        bus._conn.execute(
            "UPDATE delivery_attempts SET next_retry_at = '' WHERE message_id = ?",
            (mid,),
        )
        bus._conn.commit()
        pending = bus.pending_deliveries()
        assert len(pending) == 1
        assert pending[0]["message_id"] == mid
        assert pending[0]["recipient"] == "lingclaude"
        assert pending[0]["sender"] == "lingflow"

    def test_excludes_confirmed(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        bus.confirm_delivery(mid, "lingclaude")
        assert bus.pending_deliveries() == []

    def test_excludes_escalated(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        for _ in range(3):
            bus.confirm_delivery(mid, "lingclaude")
        assert bus.pending_deliveries() == []


class TestDeliveryStats:
    def test_stats_includes_delivery_keys(self, bus: LingBus) -> None:
        s = bus.stats()
        assert "delivery_pending" in s
        assert "delivery_confirmed" in s
        assert "delivery_escalated" in s

    def test_stats_counts(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.confirm_delivery(mid, "lingclaude")
        s = bus.stats()
        assert s["delivery_pending"] == 1
        assert s["delivery_confirmed"] == 0
        assert s["delivery_escalated"] == 0

    def test_stats_confirmed_count(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="delivery-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        bus.confirm_delivery(mid, "lingclaude")
        s = bus.stats()
        assert s["delivery_confirmed"] == 1


class TestGetUnreadSummary:
    def test_empty_summary(self, bus: LingBus) -> None:
        summary = bus.get_unread_summary("lingclaude")
        assert summary["count"] == 0
        assert summary["latest"] is None
        assert summary["by_channel"] == {}

    def test_unacked_count(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="unread-test", sender="lingflow", recipients=["lingclaude"],
        )
        summary = bus.get_unread_summary("lingclaude")
        assert summary["count"] >= 1
        assert summary["latest"] is not None
        assert summary["latest"]["sender"] == "lingflow"

    def test_since_rowid(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="old", sender="lingflow", recipients=["lingclaude"],
        )
        max_rid = bus.get_max_rowid("lingclaude")
        bus.open_thread(
            topic="new", sender="lingflow", recipients=["lingclaude"],
        )
        summary = bus.get_unread_summary("lingclaude", since_rowid=max_rid)
        assert summary["count"] >= 1
        assert summary["latest"]["subject"] == "new"

    def test_by_channel(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="ch-test", sender="lingflow", recipients=["lingclaude"],
            channel="knowledge",
        )
        summary = bus.get_unread_summary("lingclaude")
        assert "knowledge" in summary["by_channel"]

    def test_acked_excluded(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="ack-test", sender="lingflow", recipients=["lingclaude"],
        )
        bus.ack(mid, "lingclaude")
        summary = bus.get_unread_summary("lingclaude")
        assert summary["count"] == 0


class TestGetGlobalMaxRowid:
    def test_zero_when_empty(self, bus: LingBus) -> None:
        assert bus.get_global_max_rowid() == 0

    def test_increases_after_post(self, bus: LingBus) -> None:
        bus.open_thread(topic="t1", sender="lingflow", recipients=["lingclaude"])
        r1 = bus.get_global_max_rowid()
        assert r1 > 0
        bus.open_thread(topic="t2", sender="lingflow", recipients=["lingclaude"])
        r2 = bus.get_global_max_rowid()
        assert r2 > r1


class TestWatchChanges:
    def test_returns_empty_when_no_new(self, bus: LingBus) -> None:
        bus.open_thread(topic="t1", sender="lingflow", recipients=["lingclaude"])
        max_rid = bus.get_global_max_rowid()
        changes = bus.watch_changes(since_rowid=max_rid)
        assert changes == []

    def test_returns_new_messages(self, bus: LingBus) -> None:
        bus.open_thread(topic="t1", sender="lingflow", recipients=["lingclaude"])
        max_rid = bus.get_global_max_rowid()
        bus.open_thread(topic="t2", sender="lingflow", recipients=["lingclaude"])
        changes = bus.watch_changes(since_rowid=max_rid)
        assert len(changes) >= 1
        assert changes[0].topic if hasattr(changes[0], 'topic') else True

    def test_ordered_oldest_first(self, bus: LingBus) -> None:
        bus.open_thread(topic="first", sender="lingflow", recipients=["lingclaude"])
        max_rid = bus.get_global_max_rowid()
        bus.open_thread(topic="second", sender="lingflow", recipients=["lingclaude"])
        bus.open_thread(topic="third", sender="lingflow", recipients=["lingclaude"])
        changes = bus.watch_changes(since_rowid=max_rid)
        assert len(changes) >= 2
        rowids = [m.rowid for m in changes]
        assert rowids == sorted(rowids)

    def test_respects_limit(self, bus: LingBus) -> None:
        max_rid = bus.get_global_max_rowid()
        for i in range(5):
            bus.open_thread(topic=f"t{i}", sender="lingflow", recipients=["lingclaude"])
        changes = bus.watch_changes(since_rowid=max_rid, limit=2)
        assert len(changes) == 2


class TestAlertSubjectDedup:
    """Alert/system channels dedup by subject to prevent storm flooding."""

    @pytest.fixture
    def throttled_bus(self, tmp_path: Path) -> LingBus:
        b = LingBus(bus_dir=tmp_path / "bus", throttle=True)
        yield b
        b.close()

    def test_same_subject_alert_deduped(self, throttled_bus: LingBus) -> None:
        throttled_bus.open_thread(
            topic="patrol", sender="lingflow_plus", recipients=["all"],
            channel="system", subject="health_patrol告警", body="check 10:00",
        )
        with pytest.raises(ValueError, match="alert_dedup"):
            throttled_bus.open_thread(
                topic="patrol", sender="lingflow_plus", recipients=["all"],
                channel="system", subject="health_patrol告警", body="check 10:05",
            )

    def test_different_subject_not_deduped(self, throttled_bus: LingBus) -> None:
        throttled_bus.open_thread(
            topic="patrol", sender="lingflow_plus", recipients=["all"],
            channel="system", subject="服务A DOWN", body="check 10:00",
        )
        throttled_bus._conn.execute("DELETE FROM rate_limits WHERE thread_id != '__alert_subject__:system'")
        tid2, _ = throttled_bus.open_thread(
            topic="patrol", sender="lingflow_plus", recipients=["all"],
            channel="system", subject="服务B DOWN", body="check 10:05",
        )
        assert tid2

    def test_ecosystem_not_deduped_by_subject(self, throttled_bus: LingBus) -> None:
        throttled_bus.open_thread(
            topic="discuss", sender="lingflow", recipients=["lingclaude"],
            channel="ecosystem", subject="同subject讨论", body="msg1",
        )
        throttled_bus._conn.execute("DELETE FROM rate_limits")
        tid2, _ = throttled_bus.open_thread(
            topic="discuss", sender="lingflow", recipients=["lingclaude"],
            channel="ecosystem", subject="同subject讨论", body="msg2",
        )
        assert tid2

    def test_alert_channel_deduped(self, throttled_bus: LingBus) -> None:
        throttled_bus.open_thread(
            topic="alert", sender="lingflow_plus", recipients=["all"],
            channel="alert", subject="[CRITICAL] :8766 DOWN", body="alert 1",
        )
        with pytest.raises(ValueError, match="alert_dedup"):
            throttled_bus.open_thread(
                topic="alert", sender="lingflow_plus", recipients=["all"],
                channel="alert", subject="[CRITICAL] :8766 DOWN", body="alert 2",
            )

    def test_empty_subject_not_deduped(self, throttled_bus: LingBus) -> None:
        tid1, _ = throttled_bus.open_thread(
            topic="alert", sender="lingflow_plus", recipients=["all"],
            channel="alert", subject="", body="body1",
        )
        throttled_bus._conn.execute("DELETE FROM rate_limits")
        tid2, _ = throttled_bus.open_thread(
            topic="alert2", sender="lingflow_plus", recipients=["all"],
            channel="alert", subject="", body="body2",
        )
        assert tid1 and tid2


class TestLargeMessageAlert:
    """>50KB messages trigger automatic alert (thinking bloat detection)."""

    @pytest.fixture
    def throttled_bus(self, tmp_path: Path) -> LingBus:
        b = LingBus(bus_dir=tmp_path / "bus", throttle=True)
        yield b
        b.close()

    def test_large_open_thread_triggers_alert(self, throttled_bus: LingBus) -> None:
        large_body = "x" * 51201  # just over 50KB
        before = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        throttled_bus.open_thread(
            topic="big", sender="lingflow", recipients=["all"],
            channel="ecosystem", subject="big msg", body=large_body,
        )
        after = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        assert after == before + 1

    def test_small_message_no_alert(self, throttled_bus: LingBus) -> None:
        before = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        throttled_bus.open_thread(
            topic="small", sender="lingflow", recipients=["all"],
            channel="ecosystem", subject="small", body="x" * 1000,
        )
        after = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        assert after == before

    def test_large_reply_triggers_alert(self, throttled_bus: LingBus) -> None:
        tid, _ = throttled_bus.open_thread(
            topic="t", sender="lingflow", recipients=["lingclaude"],
            channel="ecosystem", body="normal",
        )
        before = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        throttled_bus.post_reply(tid, sender="lingflow", recipient="lingclaude", body="x" * 52000)
        after = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        assert after == before + 1

    def test_large_msg_dedup_per_sender(self, throttled_bus: LingBus) -> None:
        tid, _ = throttled_bus.open_thread(
            topic="t", sender="lingflow", recipients=["all"],
            channel="ecosystem", body="x" * 51201,
        )
        alerts1 = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        # Clear rate_limits to bypass throttle, but large_msg dedup uses its own key
        throttled_bus._conn.execute(
            "DELETE FROM rate_limits WHERE thread_id != '__large_msg__:lingflow'"
        )
        throttled_bus._conn.commit()
        throttled_bus.post_reply(tid, sender="lingflow", recipient="all", body="y" * 53000)
        alerts2 = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        assert alerts2 == alerts1  # deduped, no new alert

    def test_large_msg_different_senders_both_alert(self, throttled_bus: LingBus) -> None:
        throttled_bus.open_thread(
            topic="t", sender="lingflow", recipients=["all"],
            channel="ecosystem", body="x" * 51201,
        )
        throttled_bus._conn.execute("DELETE FROM rate_limits")
        throttled_bus._conn.commit()
        throttled_bus.open_thread(
            topic="t2", sender="lingclaude", recipients=["all"],
            channel="ecosystem", body="y" * 51201,
        )
        alert_count = throttled_bus._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE channel='alert'"
        ).fetchone()["c"]
        assert alert_count == 2


class TestDailySenderLimit:
    """Tests for the daily sender message limit (SDTH defense)."""

    @pytest.fixture
    def throttled_bus(self, tmp_path: Path) -> LingBus:
        b = LingBus(bus_dir=tmp_path / "bus", throttle=True)
        yield b
        b.close()

    def _fill_rate_limits(self, bus: LingBus, sender: str, count: int) -> None:
        now = time.time()
        for i in range(count):
            bus._conn.execute(
                "INSERT OR REPLACE INTO rate_limits (sender, thread_id, body_hash, timestamp) VALUES (?,?,?,?)",
                (sender, f"thread_{i}", f"hash_{i}", now - i),
            )
        bus._conn.commit()

    def test_allows_below_limit(self, throttled_bus: LingBus) -> None:
        self._fill_rate_limits(throttled_bus, "lingflow", 499)
        tid, _ = throttled_bus.open_thread(
            topic="t", sender="lingflow", recipients=["all"], body="ok",
        )
        assert tid

    def test_blocks_at_daily_sender_limit(self, throttled_bus: LingBus) -> None:
        self._fill_rate_limits(throttled_bus, "lingflow", 500)
        with pytest.raises(ValueError, match="daily_sender_limit"):
            throttled_bus.open_thread(
                topic="t", sender="lingflow", recipients=["all"], body="blocked",
            )

    def test_blocks_reply_at_daily_sender_limit(self, throttled_bus: LingBus) -> None:
        self._fill_rate_limits(throttled_bus, "lingflow", 500)
        tid, _ = throttled_bus.open_thread(
            topic="t", sender="lingclaude", recipients=["all"], body="open",
        )
        throttled_bus._conn.execute("DELETE FROM rate_limits WHERE sender='lingflow'")
        throttled_bus._conn.commit()
        self._fill_rate_limits(throttled_bus, "lingflow", 500)
        with pytest.raises(ValueError, match="daily_sender_limit"):
            throttled_bus.post_reply(tid, sender="lingflow", recipient="all", body="blocked")

    def test_different_senders_independent(self, throttled_bus: LingBus) -> None:
        self._fill_rate_limits(throttled_bus, "lingflow", 500)
        tid, _ = throttled_bus.open_thread(
            topic="t", sender="lingclaude", recipients=["all"], body="ok",
        )
        assert tid


class TestUrgentPriorityPoll:
    """Tests for urgent sender priority in poll results."""

    @pytest.fixture
    def populated_bus(self, tmp_path: Path) -> LingBus:
        b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
        yield b
        b.close()

    def test_urgent_messages_first(self, populated_bus: LingBus) -> None:
        tid1, _ = populated_bus.open_thread(
            topic="sdt1", sender="lingflow", recipients=["all"],
            body="SDT output 1",
        )
        populated_bus.post_reply(tid1, sender="lingflow", recipient="all", body="SDT output 2")
        urgent_tid, _ = populated_bus.open_thread(
            topic="user_msg", sender="lingflow", recipients=["all"],
            body="placeholder",
        )
        populated_bus._conn.execute(
            "UPDATE messages SET sender='webui_user', body='hello', subject='user msg' WHERE thread_id=?",
            (urgent_tid,),
        )
        populated_bus._conn.commit()
        msgs = populated_bus.poll("lingflow", since_rowid=0, reverse=False)
        senders = [m.sender for m in msgs]
        assert senders[0] == "webui_user"

    def test_poll_urgent_returns_only_urgent(self, populated_bus: LingBus) -> None:
        populated_bus.open_thread(
            topic="sdt", sender="lingflow", recipients=["all"],
            body="SDT output",
        )
        urgent_tid, _ = populated_bus.open_thread(
            topic="user_msg", sender="lingflow", recipients=["all"],
            body="placeholder",
        )
        populated_bus._conn.execute(
            "UPDATE messages SET sender='webui_user', body='hello', subject='urgent' WHERE thread_id=?",
            (urgent_tid,),
        )
        populated_bus._conn.commit()
        urgent = populated_bus.poll_urgent("lingflow", since_rowid=0)
        assert len(urgent) == 1
        assert urgent[0].sender == "webui_user"

    def test_poll_urgent_empty_when_no_urgent(self, populated_bus: LingBus) -> None:
        populated_bus.open_thread(
            topic="sdt", sender="lingflow", recipients=["all"],
            body="SDT output",
        )
        urgent = populated_bus.poll_urgent("lingflow", since_rowid=0)
        assert len(urgent) == 0


class TestVerifyWriteAuth:
    """Tests for identity file write authorization."""

    @pytest.fixture
    def auth_bus(self, tmp_path: Path) -> LingBus:
        b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
        yield b
        b.close()

    def test_not_protected_file_passes(self, auth_bus: LingBus) -> None:
        result = auth_bus.verify_write_auth("/home/ai/lingmessage/handover.md", "lingflow")
        assert result["authorized"] is True
        assert result["source"] == "not_protected"

    def test_lingmessage_cannot_self_authorize(self, auth_bus: LingBus) -> None:
        result = auth_bus.verify_write_auth("/home/ai/lingmessage/CRUSH.md", "lingmessage")
        assert result["authorized"] is False
        assert result["source"] == "self_review_excluded"

    def test_no_auth_rejected(self, auth_bus: LingBus) -> None:
        result = auth_bus.verify_write_auth("/home/ai/lingflow/CRUSH.md", "lingflow")
        assert result["authorized"] is False
        assert "POST_TO_LINGBUS_FIRST" in result["reason"]

    def test_user_message_authorizes(self, auth_bus: LingBus) -> None:
        auth_bus.open_thread(
            topic="user chat", sender="lingflow", recipients=["all"], body="hi",
        )
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        auth_bus._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp) "
            "SELECT 'm1', thread_id, 'webui_user', 'lingflow', 'open', 'ecosystem', 'hi', 'go ahead', ? "
            "FROM threads LIMIT 1",
            (ts,),
        )
        auth_bus._conn.commit()
        result = auth_bus.verify_write_auth("/home/ai/lingflow/CRUSH.md", "lingflow", "update SDT rules")
        assert result["authorized"] is True
        assert result["source"] == "user_message"
        assert result["auth_id"]

    def test_member_confirmation_authorizes(self, auth_bus: LingBus) -> None:
        tid, _ = auth_bus.open_thread(
            topic="intent to modify CRUSH.md",
            sender="lingflow", recipients=["all"],
            body="I need to update CRUSH.md with new SDT rules",
        )
        auth_bus.post_reply(tid, sender="lingclaude", recipient="all", body="同意，可以修改")
        result = auth_bus.verify_write_auth("/home/ai/lingflow/CRUSH.md", "lingflow", "update SDT rules")
        assert result["authorized"] is True
        assert result["source"] == "member_confirmation"

    def test_prior_auth_reused(self, auth_bus: LingBus) -> None:
        auth_bus.open_thread(
            topic="user chat", sender="lingflow", recipients=["all"], body="hi",
        )
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        auth_bus._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp) "
            "SELECT 'm2', thread_id, 'webui_user', 'lingflow', 'open', 'ecosystem', 'hi', 'go', ? "
            "FROM threads LIMIT 1",
            (ts,),
        )
        auth_bus._conn.commit()
        r1 = auth_bus.verify_write_auth("/home/ai/lingflow/CRUSH.md", "lingflow", "update")
        assert r1["authorized"] is True
        r2 = auth_bus.verify_write_auth("/home/ai/lingflow/CRUSH.md", "lingflow", "update again")
        assert r2["authorized"] is True

    def test_agents_md_also_protected(self, auth_bus: LingBus) -> None:
        result = auth_bus.verify_write_auth("/home/ai/lingflow/AGENTS.md", "lingflow")
        assert result["authorized"] is False
        assert "POST_TO_LINGBUS_FIRST" in result["reason"]
