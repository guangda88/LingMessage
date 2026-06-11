from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lingmessage.lingbus import LingBus


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
    yield b
    b.close()


class TestLingBusStorePending:
    def test_store_get_pending(self, tmp_path: Path) -> None:
        from lingmessage.store import LingBusStore
        bus = LingBus(bus_dir=tmp_path / "bus2", throttle=False)
        store = LingBusStore(bus)
        bus.open_thread(
            topic="test", sender="lingflow", recipients=["lingclaude"], body="hello",
        )
        pending = store.get_pending("lingclaude")
        assert len(pending) == 1
        assert pending[0]["body"] == "hello"
        bus.close()

    def test_store_batch_ack(self, tmp_path: Path) -> None:
        from lingmessage.store import LingBusStore
        bus = LingBus(bus_dir=tmp_path / "bus3", throttle=False)
        store = LingBusStore(bus)
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        bus.open_thread(
            topic="t2", sender="lingflow", recipients=["lingclaude"], body="m2",
        )
        assert store.pending_count("lingclaude") == 2
        count = store.batch_ack("lingclaude")
        assert count == 2
        assert store.pending_count("lingclaude") == 0
        bus.close()

    def test_store_pending_count(self, tmp_path: Path) -> None:
        from lingmessage.store import LingBusStore
        bus = LingBus(bus_dir=tmp_path / "bus4", throttle=False)
        store = LingBusStore(bus)
        assert store.pending_count("lingclaude") == 0
        bus.open_thread(
            topic="t1", sender="lingflow", recipients=["lingclaude"], body="m1",
        )
        assert store.pending_count("lingclaude") == 1
        bus.close()


class TestDiscoverMemberDirs:
    def test_discovers_dirs_with_crush(self, tmp_path: Path) -> None:
        from lingmessage.constraint_hash import discover_member_dirs
        (tmp_path / "lingtest").mkdir()
        (tmp_path / "lingtest" / "CRUSH.md").write_text("# test", encoding="utf-8")
        (tmp_path / "other_dir").mkdir()
        with patch("lingmessage.constraint_hash._BASE_DIR", tmp_path):
            dirs = discover_member_dirs()
        assert "lingtest" in dirs
        assert "other_dir" not in dirs

    def test_discovers_dirs_with_agents(self, tmp_path: Path) -> None:
        from lingmessage.constraint_hash import discover_member_dirs
        (tmp_path / "lingagent").mkdir()
        (tmp_path / "lingagent" / "AGENTS.md").write_text("# test", encoding="utf-8")
        with patch("lingmessage.constraint_hash._BASE_DIR", tmp_path):
            dirs = discover_member_dirs()
        assert "lingagent" in dirs

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        from lingmessage.constraint_hash import discover_member_dirs
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "CRUSH.md").write_text("# test", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "CRUSH.md").write_text("# test", encoding="utf-8")
        with patch("lingmessage.constraint_hash._BASE_DIR", tmp_path):
            dirs = discover_member_dirs()
        assert len(dirs) == 0

    def test_empty_when_no_base(self, tmp_path: Path) -> None:
        from lingmessage.constraint_hash import discover_member_dirs
        with patch("lingmessage.constraint_hash._BASE_DIR", tmp_path / "nonexistent"):
            dirs = discover_member_dirs()
        assert dirs == {}


class TestQueuePendingNoFK:
    def test_queue_without_message_row(self, bus: LingBus) -> None:
        count = bus.queue_pending("fake_message_id_123", ["lingclaude"])
        assert count == 1
        assert bus.pending_count("lingclaude") == 1

    def test_get_pending_without_message_returns_empty(self, bus: LingBus) -> None:
        bus.queue_pending("fake_mid", ["lingclaude"])
        pending = bus.get_pending("lingclaude")
        assert len(pending) == 0

    def test_batch_ack_without_message(self, bus: LingBus) -> None:
        bus.queue_pending("fake_mid", ["lingclaude"])
        count = bus.batch_ack("lingclaude")
        assert count == 1
        assert bus.pending_count("lingclaude") == 0
