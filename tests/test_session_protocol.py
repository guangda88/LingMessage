"""Tests for SessionProtocol ABC."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lingmessage.session_protocol import (
    SessionMetadata,
    SessionProtocol,
    SessionStatus,
)


class StubSessionProtocol(SessionProtocol):
    """Minimal concrete implementation for testing."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}
        self._metadata: dict[str, SessionMetadata] = {}
        self._counter = 0
        self._closed = False

    def create(self, member_id: str, **kwargs) -> SessionMetadata:
        self._counter += 1
        sid = f"sess-{self._counter}"
        now = datetime.now(timezone.utc).isoformat()
        meta = SessionMetadata(
            session_id=sid,
            member_id=member_id,
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            extra=kwargs or None,
        )
        self._metadata[sid] = meta
        self._sessions[sid] = {"member_id": member_id, "messages": []}
        return meta

    def checkpoint(self, session_id: str, data: dict) -> SessionMetadata:
        if session_id not in self._metadata:
            raise KeyError(session_id)
        old = self._metadata[session_id]
        now = datetime.now(timezone.utc).isoformat()
        self._sessions[session_id].update(data)
        meta = SessionMetadata(
            session_id=session_id,
            member_id=old.member_id,
            status=SessionStatus.CHECKPOINTED,
            created_at=old.created_at,
            updated_at=now,
            message_count=len(self._sessions[session_id].get("messages", [])),
        )
        self._metadata[session_id] = meta
        return meta

    def restore(self, session_id: str) -> dict:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return dict(self._sessions[session_id])

    def archive(self, session_id: str) -> SessionMetadata:
        if session_id not in self._metadata:
            raise KeyError(session_id)
        old = self._metadata[session_id]
        now = datetime.now(timezone.utc).isoformat()
        meta = SessionMetadata(
            session_id=session_id,
            member_id=old.member_id,
            status=SessionStatus.ARCHIVED,
            created_at=old.created_at,
            updated_at=now,
            message_count=old.message_count,
        )
        self._metadata[session_id] = meta
        return meta

    def expire(self, session_id: str) -> SessionMetadata:
        if session_id not in self._metadata:
            raise KeyError(session_id)
        old = self._metadata[session_id]
        now = datetime.now(timezone.utc).isoformat()
        meta = SessionMetadata(
            session_id=session_id,
            member_id=old.member_id,
            status=SessionStatus.EXPIRED,
            created_at=old.created_at,
            updated_at=now,
        )
        self._metadata[session_id] = meta
        return meta

    def get_metadata(self, session_id: str) -> SessionMetadata:
        if session_id not in self._metadata:
            raise KeyError(session_id)
        return self._metadata[session_id]

    def list_sessions(
        self,
        member_id: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionMetadata]:
        results = list(self._metadata.values())
        if member_id:
            results = [m for m in results if m.member_id == member_id]
        if status:
            results = [m for m in results if m.status == status]
        return results

    def close(self) -> None:
        self._closed = True


class TestSessionStatus:
    def test_all_values(self) -> None:
        assert set(SessionStatus) == {
            SessionStatus.ACTIVE,
            SessionStatus.CHECKPOINTED,
            SessionStatus.ARCHIVED,
            SessionStatus.EXPIRED,
        }

    def test_str_enum(self) -> None:
        assert SessionStatus.ACTIVE == "active"
        assert SessionStatus.ARCHIVED == "archived"


class TestSessionMetadata:
    def test_frozen(self) -> None:
        meta = SessionMetadata(
            session_id="s1",
            member_id="lingclaude",
            status=SessionStatus.ACTIVE,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        with pytest.raises(AttributeError):
            meta.status = SessionStatus.ARCHIVED

    def test_defaults(self) -> None:
        meta = SessionMetadata(
            session_id="s1",
            member_id="lingclaude",
            status=SessionStatus.ACTIVE,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert meta.message_count == 0
        assert meta.size_bytes == 0
        assert meta.extra is None

    def test_with_extra(self) -> None:
        meta = SessionMetadata(
            session_id="s1",
            member_id="lingclaude",
            status=SessionStatus.ACTIVE,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            message_count=42,
            size_bytes=1024,
            extra={"thread_id": "abc"},
        )
        assert meta.message_count == 42
        assert meta.extra == {"thread_id": "abc"}


class TestStubProtocol:
    def test_create_and_get(self) -> None:
        p = StubSessionProtocol()
        meta = p.create("lingclaude")
        assert meta.member_id == "lingclaude"
        assert meta.status == SessionStatus.ACTIVE
        assert p.get_metadata(meta.session_id) == meta

    def test_restore(self) -> None:
        p = StubSessionProtocol()
        meta = p.create("lingclaude")
        data = p.restore(meta.session_id)
        assert data["member_id"] == "lingclaude"

    def test_restore_missing_raises(self) -> None:
        p = StubSessionProtocol()
        with pytest.raises(KeyError):
            p.restore("nonexistent")

    def test_checkpoint(self) -> None:
        p = StubSessionProtocol()
        meta = p.create("lingclaude")
        updated = p.checkpoint(meta.session_id, {"messages": ["a", "b"]})
        assert updated.status == SessionStatus.CHECKPOINTED
        assert updated.message_count == 2

    def test_archive(self) -> None:
        p = StubSessionProtocol()
        meta = p.create("lingclaude")
        archived = p.archive(meta.session_id)
        assert archived.status == SessionStatus.ARCHIVED

    def test_expire(self) -> None:
        p = StubSessionProtocol()
        meta = p.create("lingclaude")
        expired = p.expire(meta.session_id)
        assert expired.status == SessionStatus.EXPIRED

    def test_lifecycle(self) -> None:
        p = StubSessionProtocol()
        m = p.create("lingclaude")
        assert m.status == SessionStatus.ACTIVE
        m = p.checkpoint(m.session_id, {"messages": ["hello"]})
        assert m.status == SessionStatus.CHECKPOINTED
        m = p.archive(m.session_id)
        assert m.status == SessionStatus.ARCHIVED
        m = p.expire(m.session_id)
        assert m.status == SessionStatus.EXPIRED

    def test_list_sessions_filter(self) -> None:
        p = StubSessionProtocol()
        p.create("lingclaude")
        p.create("lingflow")
        p.create("lingclaude")
        assert len(p.list_sessions(member_id="lingclaude")) == 2
        assert len(p.list_sessions(member_id="lingflow")) == 1
        assert len(p.list_sessions()) == 3

    def test_list_sessions_status_filter(self) -> None:
        p = StubSessionProtocol()
        m = p.create("lingclaude")
        p.create("lingflow")
        p.archive(m.session_id)
        active = p.list_sessions(status=SessionStatus.ACTIVE)
        assert len(active) == 1
        archived = p.list_sessions(status=SessionStatus.ARCHIVED)
        assert len(archived) == 1

    def test_close(self) -> None:
        p = StubSessionProtocol()
        p.close()
        assert p._closed

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            SessionProtocol()
