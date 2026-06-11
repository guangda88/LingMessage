from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.lingbus import LingBus


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
    yield b
    b.close()


class TestQueuePending:
    def test_queue_for_recipients(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude", "lingzhi"],
            body="hello",
        )
        pending = bus.get_pending("lingclaude")
        assert len(pending) == 1
        assert pending[0]["message_id"] == mid
        assert pending[0]["body"] == "hello"

    def test_queue_skips_all(self, bus: LingBus) -> None:
        count = bus.queue_pending("msg123", ["all"])
        assert count == 0

    def test_queue_idempotent(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude"], body="hello",
        )
        bus.queue_pending(mid, ["lingclaude"])
        pending = bus.get_pending("lingclaude")
        assert len(pending) == 1

    def test_queue_multiple_messages(self, bus: LingBus) -> None:
        _, mid1 = bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        _, mid2 = bus.open_thread(
            topic="t2", sender="lingflow", recipients=["lingclaude"], body="m2",
        )
        _, mid3 = bus.open_thread(
            topic="t3", sender="lingflow", recipients=["lingclaude"], body="m3",
        )
        assert bus.pending_count("lingclaude") == 3


class TestBatchAck:
    def test_batch_ack_clears_pending(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        bus.open_thread(
            topic="t2", sender="lingflow", recipients=["lingclaude"], body="m2",
        )
        assert bus.pending_count("lingclaude") == 2
        count = bus.batch_ack("lingclaude")
        assert count == 2
        assert bus.pending_count("lingclaude") == 0

    def test_batch_ack_empty(self, bus: LingBus) -> None:
        count = bus.batch_ack("lingclaude")
        assert count == 0

    def test_batch_ack_updates_acked_by(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude"], body="hello",
        )
        bus.batch_ack("lingclaude")
        row = bus._conn.execute(
            "SELECT acked_by FROM messages WHERE message_id = ?", (mid,)
        ).fetchone()
        import json
        acked = json.loads(row["acked_by"])
        assert "lingclaude" in acked

    def test_batch_ack_only_affects_target_member(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude", "lingzhi"],
            body="hello",
        )
        assert bus.pending_count("lingclaude") == 1
        assert bus.pending_count("lingzhi") == 1
        bus.batch_ack("lingclaude")
        assert bus.pending_count("lingclaude") == 0
        assert bus.pending_count("lingzhi") == 1


class TestGetPending:
    def test_returns_unacked_only(self, bus: LingBus) -> None:
        tid1, mid1 = bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="first",
        )
        bus.open_thread(
            topic="t2", sender="lingflow", recipients=["lingclaude"], body="second",
        )
        bus.batch_ack("lingclaude")
        pending = bus.get_pending("lingclaude")
        assert len(pending) == 0

    def test_returns_message_details(self, bus: LingBus) -> None:
        tid, mid = bus.open_thread(
            topic="test topic", sender="lingflow", recipients=["lingclaude"],
            body="hello world", channel="ecosystem", subject="test sub",
        )
        pending = bus.get_pending("lingclaude")
        assert len(pending) == 1
        p = pending[0]
        assert p["message_id"] == mid
        assert p["thread_id"] == tid
        assert p["sender"] == "lingflow"
        assert p["subject"] == "test sub"
        assert p["body"] == "hello world"
        assert p["channel"] == "ecosystem"

    def test_limit_parameter(self, bus: LingBus) -> None:
        for i in range(5):
            bus.open_thread(
                topic=f"t{i}", sender="lingflow", recipients=["lingclaude"],
                body=f"msg{i}",
            )
        pending = bus.get_pending("lingclaude", limit=3)
        assert len(pending) == 3


class TestPendingCount:
    def test_zero_when_empty(self, bus: LingBus) -> None:
        assert bus.pending_count("lingclaude") == 0

    def test_counts_unacked(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        bus.open_thread(
            topic="t2", sender="lingflow", recipients=["lingclaude"], body="m2",
        )
        assert bus.pending_count("lingclaude") == 2

    def test_excludes_acked(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        bus.batch_ack("lingclaude")
        assert bus.pending_count("lingclaude") == 0


class TestPrunePending:
    def test_prune_acked_old(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        bus.batch_ack("lingclaude")
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        bus._conn.execute(
            "UPDATE pending_for SET acked_at = ? WHERE acked = 1", (old,)
        )
        bus._conn.commit()
        pruned = bus.prune_pending(older_than_days=30)
        assert pruned == 1

    def test_prune_does_not_affect_unacked(self, bus: LingBus) -> None:
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        pruned = bus.prune_pending(older_than_days=0)
        assert pruned == 0
        assert bus.pending_count("lingclaude") == 1


class TestPostReplyAutoPending:
    def test_reply_to_specific_recipient_queues(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude"],
        )
        mid = bus.post_reply(
            thread_id=tid, sender="lingclaude", recipient="lingflow", body="reply",
        )
        assert bus.pending_count("lingflow") == 1

    def test_reply_to_all_queues_for_participants(self, bus: LingBus) -> None:
        tid, _ = bus.open_thread(
            topic="test", sender="lingflow",
            recipients=["lingclaude", "lingzhi"],
        )
        bus.batch_ack("lingflow")
        bus.batch_ack("lingclaude")
        bus.batch_ack("lingzhi")
        bus.post_reply(
            thread_id=tid, sender="lingclaude", recipient="all", body="broadcast",
        )
        assert bus.pending_count("lingflow") == 1
        assert bus.pending_count("lingzhi") == 1
        assert bus.pending_count("lingclaude") == 0
