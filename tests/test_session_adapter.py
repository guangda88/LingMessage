"""Tests for FamilySessionManager — unified session management with auto-compression."""

from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.session_adapter import (
    FamilySessionProtocolAdapter,
    _make_session_id,
    _parse_session_id,
)
from lingmessage.session_compression import CompressionConfig
from lingmessage.session_manager import FamilySessionManager, get_session_manager
from lingmessage.session_protocol import SessionStatus


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_sessions.db"


@pytest.fixture
def adapter(tmp_db: Path) -> FamilySessionProtocolAdapter:
    manager = FamilySessionManager(db_path=tmp_db)
    return FamilySessionProtocolAdapter(manager)


class TestSessionIdHelpers:
    def test_make_session_id(self) -> None:
        assert _make_session_id("lingclaude", "default") == "lingclaude:default"

    def test_make_session_id_custom_slot(self) -> None:
        assert _make_session_id("lingflow", "slot-2") == "lingflow:slot-2"

    def test_parse_session_id(self) -> None:
        member, slot = _parse_session_id("lingclaude:default")
        assert member == "lingclaude"
        assert slot == "default"

    def test_parse_session_id_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            _parse_session_id("no-colon-here")

    def test_roundtrip(self) -> None:
        sid = _make_session_id("lingresearch", "debug")
        member, slot = _parse_session_id(sid)
        assert member == "lingresearch"
        assert slot == "debug"


class TestCreateAndRestore:
    def test_create_returns_metadata(self, adapter: FamilySessionProtocolAdapter) -> None:
        meta = adapter.create("lingclaude")
        assert meta.member_id == "lingclaude"
        assert meta.status == SessionStatus.ACTIVE
        assert meta.session_id == "lingclaude:default"
        assert meta.message_count == 0

    def test_create_with_slot(self, adapter: FamilySessionProtocolAdapter) -> None:
        meta = adapter.create("lingflow", slot_id="prod")
        assert meta.session_id == "lingflow:prod"

    def test_create_with_history(self, adapter: FamilySessionProtocolAdapter) -> None:
        history = [{"role": "user", "content": "hello"}]
        meta = adapter.create(
            "lingclaude",
            conversation_history=history,
            adapter_state={"key": "val"},
        )
        assert meta.message_count == 1
        assert meta.size_bytes > 0

    def test_restore_returns_dict(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create("lingclaude", session_key="abc123")
        data = adapter.restore("lingclaude:default")
        assert data["member_id"] == "lingclaude"
        assert data["session_key"] == "abc123"
        assert isinstance(data["conversation_history"], list)

    def test_restore_not_found(self, adapter: FamilySessionProtocolAdapter) -> None:
        with pytest.raises(KeyError):
            adapter.restore("nonexistent:default")

    def test_create_restore_roundtrip(self, adapter: FamilySessionProtocolAdapter) -> None:
        history = [
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
        ]
        adapter.create("lingresearch", conversation_history=history)
        data = adapter.restore("lingresearch:default")
        assert len(data["conversation_history"]) == 2
        assert data["conversation_history"][0]["content"] == "ping"


class TestCheckpoint:
    def test_checkpoint_updates_status(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        meta = adapter.checkpoint("lingclaude:default", {"session_key": "ckpt-1"})
        assert meta.status == SessionStatus.CHECKPOINTED

    def test_checkpoint_preserves_data(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude", session_key="original")
        adapter.checkpoint(
            "lingclaude:default",
            {"session_key": "updated", "adapter_state": {"step": 5}},
        )
        data = adapter.restore("lingclaude:default")
        assert data["session_key"] == "updated"
        assert data["adapter_state"]["step"] == 5

    def test_checkpoint_not_found(self, adapter: FamilySessionProtocolAdapter) -> None:
        with pytest.raises(KeyError):
            adapter.checkpoint("nobody:default", {})


class TestArchive:
    def test_archive_changes_status(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create("lingclaude")
        meta = adapter.archive("lingclaude:default")
        assert meta.status == SessionStatus.ARCHIVED

    def test_archive_data_still_restorable(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude", session_key="keep-me")
        adapter.archive("lingclaude:default")
        data = adapter.restore("lingclaude:default")
        assert data["session_key"] == "keep-me"

    def test_archive_not_found(self, adapter: FamilySessionProtocolAdapter) -> None:
        with pytest.raises(KeyError):
            adapter.archive("ghost:default")


class TestExpire:
    def test_expire_deletes_data(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create("lingclaude", session_key="bye")
        meta = adapter.expire("lingclaude:default")
        assert meta.status == SessionStatus.EXPIRED
        assert meta.session_id == "lingclaude:default"

    def test_expire_session_gone_from_db(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        adapter.expire("lingclaude:default")
        with pytest.raises(KeyError):
            adapter.restore("lingclaude:default")

    def test_expire_not_found(self, adapter: FamilySessionProtocolAdapter) -> None:
        with pytest.raises(KeyError):
            adapter.expire("nobody:default")


class TestGetMetadata:
    def test_metadata_fields(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create(
            "lingclaude",
            conversation_history=[{"role": "user", "content": "hi"}],
        )
        meta = adapter.get_metadata("lingclaude:default")
        assert meta.member_id == "lingclaude"
        assert meta.status == SessionStatus.ACTIVE
        assert meta.message_count == 1
        assert meta.size_bytes > 0
        assert meta.extra is not None
        assert meta.extra["slot_id"] == "default"

    def test_metadata_after_checkpoint(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        adapter.checkpoint("lingclaude:default", {})
        meta = adapter.get_metadata("lingclaude:default")
        assert meta.status == SessionStatus.CHECKPOINTED

    def test_metadata_not_found(self, adapter: FamilySessionProtocolAdapter) -> None:
        with pytest.raises(KeyError):
            adapter.get_metadata("ghost:default")


class TestListSessions:
    def test_list_all(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create("lingclaude")
        adapter.create("lingflow")
        sessions = adapter.list_sessions()
        assert len(sessions) == 2

    def test_list_filter_member(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        adapter.create("lingflow")
        sessions = adapter.list_sessions(member_id="lingclaude")
        assert len(sessions) == 1
        assert sessions[0].member_id == "lingclaude"

    def test_list_filter_status(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        adapter.create("lingflow")
        adapter.archive("lingclaude:default")

        active = adapter.list_sessions(status=SessionStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].member_id == "lingflow"

        archived = adapter.list_sessions(status=SessionStatus.ARCHIVED)
        assert len(archived) == 1
        assert archived[0].member_id == "lingclaude"

    def test_list_empty(self, adapter: FamilySessionProtocolAdapter) -> None:
        assert adapter.list_sessions() == []

    def test_list_member_and_status(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude")
        adapter.create("lingflow")
        adapter.archive("lingclaude:default")

        result = adapter.list_sessions(
            member_id="lingclaude", status=SessionStatus.ARCHIVED
        )
        assert len(result) == 1

        result = adapter.list_sessions(
            member_id="lingclaude", status=SessionStatus.ACTIVE
        )
        assert len(result) == 0


class TestClose:
    def test_close_releases(self, adapter: FamilySessionProtocolAdapter) -> None:
        adapter.create("lingclaude")
        adapter.close()
        assert adapter._manager._conn is None


class TestMultipleSlots:
    def test_different_slots_independent(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude", slot_id="default", session_key="a")
        adapter.create("lingclaude", slot_id="debug", session_key="b")

        d1 = adapter.restore("lingclaude:default")
        d2 = adapter.restore("lingclaude:debug")
        assert d1["session_key"] == "a"
        assert d2["session_key"] == "b"

    def test_archive_one_slot_other_unaffected(
        self, adapter: FamilySessionProtocolAdapter
    ) -> None:
        adapter.create("lingclaude", slot_id="default")
        adapter.create("lingclaude", slot_id="debug")
        adapter.archive("lingclaude:default")

        meta_d = adapter.get_metadata("lingclaude:debug")
        assert meta_d.status == SessionStatus.ACTIVE

        meta_a = adapter.get_metadata("lingclaude:default")
        assert meta_a.status == SessionStatus.ARCHIVED


class TestAutoCompression:
    """Tests for auto-compression wired into FamilySessionManager.save_session()."""

    @pytest.fixture
    def mgr(self, tmp_path: Path) -> FamilySessionManager:
        return FamilySessionManager(
            db_path=tmp_path / "compress.db",
            compression_config=CompressionConfig(max_messages=10),
        )

    def _make_history(self, n: int) -> list[dict]:
        return [{"role": "user", "content": f"message {i}"} for i in range(n)]

    def test_below_threshold_no_compression(self, mgr: FamilySessionManager) -> None:
        history = self._make_history(5)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        assert len(loaded.conversation_history) == 5
        assert loaded.conversation_history[0]["content"] == "message 0"

    def test_above_threshold_triggers_compression(self, mgr: FamilySessionManager) -> None:
        history = self._make_history(20)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        stored = loaded.conversation_history
        # summary (1) + kept messages (10) = 11
        assert len(stored) == 11

    def test_summary_is_dict_format(self, mgr: FamilySessionManager) -> None:
        history = self._make_history(20)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        first = loaded.conversation_history[0]
        assert isinstance(first, dict)
        assert first["role"] == "system"
        assert "message" not in first.get("content", "").lower() or "压缩" in first["content"]

    def test_kept_messages_preserved(self, mgr: FamilySessionManager) -> None:
        history = self._make_history(20)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        stored = loaded.conversation_history
        # Last 10 messages should be kept (indices 10-19 -> stored[1:11])
        assert stored[1]["content"] == "message 10"
        assert stored[-1]["content"] == "message 19"

    def test_facts_stored_in_adapter_state(self, mgr: FamilySessionManager) -> None:
        history = [
            {"role": "user", "content": "I decided to use pytest for testing"},
            {"role": "assistant", "content": "排除 unittest 方案，错误: import failed"},
        ] + self._make_history(20)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        state = loaded.adapter_state
        assert "_compression_facts" in state
        facts = state["_compression_facts"]
        assert "decisions" in facts
        assert "exclusions" in facts
        assert "errors" in facts

    def test_dropped_count_stored(self, mgr: FamilySessionManager) -> None:
        history = self._make_history(25)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        assert loaded.adapter_state["_compression_dropped"] == 15

    def test_no_compression_when_config_none(self, tmp_path: Path) -> None:
        mgr = FamilySessionManager(db_path=tmp_path / "nocomp.db")
        history = self._make_history(50)
        mgr.save_session("test", conversation_history=history)
        loaded = mgr.load_session("test")
        assert loaded is not None
        assert len(loaded.conversation_history) == 50

    def test_save_load_roundtrip_with_compression(self, mgr: FamilySessionManager) -> None:
        history = [
            {"role": "user", "content": "Read src/main.py and decided to refactor"},
        ] + self._make_history(20)
        state = {"custom_key": "custom_value"}
        mgr.save_session("test", conversation_history=history, adapter_state=state)
        loaded = mgr.load_session("test")
        assert loaded is not None
        assert loaded.adapter_state["custom_key"] == "custom_value"
        assert "_compression_facts" in loaded.adapter_state
        assert len(loaded.conversation_history) == 11

    def test_existing_state_not_overwritten(self, mgr: FamilySessionManager) -> None:
        state = {"important": "data"}
        history = self._make_history(20)
        mgr.save_session("test", conversation_history=history, adapter_state=state)
        loaded = mgr.load_session("test")
        assert loaded is not None
        assert loaded.adapter_state["important"] == "data"
        assert "_compression_facts" in loaded.adapter_state


class TestGetSessionManagerSingleton:
    """Verify get_session_manager() returns a manager with auto-compression enabled."""

    def setup_method(self) -> None:
        import lingmessage.session_manager as mod
        self._prev = mod._manager
        mod._manager = None

    def teardown_method(self) -> None:
        import lingmessage.session_manager as mod
        mod._manager = self._prev

    def test_singleton_has_compression_config(self) -> None:
        mgr = get_session_manager()
        assert mgr._compression_config is not None
        assert mgr._compression_config.max_messages == 24

    def test_singleton_auto_compresses(self, tmp_path: Path) -> None:
        import lingmessage.session_manager as mod
        mgr = FamilySessionManager(
            db_path=tmp_path / "singleton_test.db",
            compression_config=CompressionConfig(max_messages=10),
        )
        mod._manager = mgr
        assert get_session_manager() is mgr
        history = [{"role": "user", "content": f"msg {i}"} for i in range(30)]
        mgr.save_session("singleton", conversation_history=history)
        loaded = mgr.load_session("singleton")
        assert loaded is not None
        assert len(loaded.conversation_history) < 30
