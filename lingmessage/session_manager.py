"""Family Session Manager — Unified session management for 灵族 members.

Implements SessionProtocol directly with SQLite backend.
Status (ACTIVE/CHECKPOINTED/ARCHIVED/EXPIRED) is persisted in the database,
surviving process restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session_compression import (
    CompressionConfig,
    auto_compress_history,
)
from .session_protocol import SessionMetadata, SessionProtocol, SessionStatus

logger = logging.getLogger("lingmessage.session_manager")

DEFAULT_COMPRESSION_CONFIG = CompressionConfig(max_messages=24)

DEFAULT_DB_PATH = Path.home() / ".lingmessage" / "family_sessions.db"


def _make_session_id(member_id: str, slot_id: str) -> str:
    return f"{member_id}:{slot_id}"


def _parse_session_id(session_id: str) -> tuple[str, str]:
    parts = session_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid session_id format: {session_id!r}")
    return parts[0], parts[1]


def _state_to_metadata(
    state: SessionState,
    status_override: SessionStatus | None = None,
) -> SessionMetadata:
    history_size = len(json.dumps(state.conversation_history, ensure_ascii=False))
    state_size = len(json.dumps(state.adapter_state, ensure_ascii=False))
    return SessionMetadata(
        session_id=_make_session_id(state.member_id, state.slot_id),
        member_id=state.member_id,
        status=status_override or SessionStatus(state.status),
        created_at=datetime.fromtimestamp(
            state.created_at, tz=timezone.utc
        ).isoformat(),
        updated_at=datetime.fromtimestamp(
            state.updated_at, tz=timezone.utc
        ).isoformat(),
        message_count=len(state.conversation_history),
        size_bytes=history_size + state_size,
        extra={
            "slot_id": state.slot_id,
            "session_key": state.session_key,
            "thread_id": state.thread_id,
            "db_row_id": state.id,
        },
    )


@dataclass
class SessionState:
    """Persisted state for a member's conversation session."""
    id: int = 0
    member_id: str = ""
    slot_id: str = "default"
    session_key: str = ""
    thread_id: str = ""
    conversation_history: list[dict] = field(default_factory=list)
    adapter_state: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "active"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "member_id": self.member_id,
            "slot_id": self.slot_id,
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "conversation_history": self.conversation_history,
            "adapter_state": self.adapter_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }


class FamilySessionManager(SessionProtocol):
    """SQLite-backed session persistence implementing SessionProtocol.

    Stores conversation state with lifecycle status so that sessions
    can be restored after adapter restarts or system reboots.
    Status is persisted in the database (not in-memory).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        compression_config: CompressionConfig | None = None,
    ) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._compression_config = compression_config
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS family_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL,
                slot_id TEXT NOT NULL DEFAULT 'default',
                session_key TEXT NOT NULL DEFAULT '',
                thread_id TEXT NOT NULL DEFAULT '',
                conversation_history TEXT NOT NULL DEFAULT '[]',
                adapter_state TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(member_id, slot_id)
            )
        """)
        try:
            conn.execute(
                "ALTER TABLE family_sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_member
            ON family_sessions(member_id)
        """)
        conn.commit()

    def _row_to_state(self, row: sqlite3.Row) -> SessionState:
        return SessionState(
            id=row["id"],
            member_id=row["member_id"],
            slot_id=row["slot_id"],
            session_key=row["session_key"],
            thread_id=row["thread_id"],
            conversation_history=json.loads(row["conversation_history"]),
            adapter_state=json.loads(row["adapter_state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
        )

    # -- Legacy API (backward compat) --

    def save_session(
        self,
        member_id: str,
        slot_id: str = "default",
        session_key: str = "",
        thread_id: str = "",
        conversation_history: list[dict] | None = None,
        adapter_state: dict | None = None,
        status: str = "active",
    ) -> int:
        """Save or update a session state. Returns the row ID.

        Auto-compresses conversation_history when it exceeds the configured
        max_messages threshold. The summary is prepended and extracted facts
        are stored in adapter_state['_compression_facts'] for cross-session recall.
        """
        history = list(conversation_history) if conversation_history else []
        state = dict(adapter_state) if adapter_state else {}

        history, state = auto_compress_history(history, state, self._compression_config)

        conn = self._get_conn()
        now = time.time()
        history_json = json.dumps(history, ensure_ascii=False)
        state_json = json.dumps(state, ensure_ascii=False)

        existing = conn.execute(
            "SELECT id FROM family_sessions WHERE member_id = ? AND slot_id = ?",
            (member_id, slot_id),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE family_sessions
                   SET session_key = ?, thread_id = ?,
                       conversation_history = ?, adapter_state = ?,
                       updated_at = ?, status = ?
                   WHERE id = ?""",
                (session_key, thread_id, history_json, state_json, now, status, existing["id"]),
            )
            conn.commit()
            return existing["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO family_sessions
                   (member_id, slot_id, session_key, thread_id,
                    conversation_history, adapter_state, created_at, updated_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (member_id, slot_id, session_key, thread_id,
                 history_json, state_json, now, now, status),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def load_session(self, member_id: str, slot_id: str = "default") -> SessionState | None:
        """Load a saved session state."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT id, member_id, slot_id, session_key, thread_id,
                      conversation_history, adapter_state, created_at, updated_at, status
               FROM family_sessions
               WHERE member_id = ? AND slot_id = ?""",
            (member_id, slot_id),
        ).fetchone()

        if not row:
            return None

        return self._row_to_state(row)

    def load_all_sessions(self, member_id: str) -> list[SessionState]:
        """Load all saved sessions for a member."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, member_id, slot_id, session_key, thread_id,
                      conversation_history, adapter_state, created_at, updated_at, status
               FROM family_sessions
               WHERE member_id = ?
               ORDER BY updated_at DESC""",
            (member_id,),
        ).fetchall()

        return [self._row_to_state(row) for row in rows]

    def append_to_history(
        self,
        member_id: str,
        role: str,
        content: str,
        slot_id: str = "default",
    ) -> None:
        """Append a message to the conversation history."""
        session = self.load_session(member_id, slot_id)
        if session is None:
            session = SessionState(member_id=member_id, slot_id=slot_id)

        session.conversation_history.append({"role": role, "content": content})
        if len(session.conversation_history) > 50:
            session.conversation_history = session.conversation_history[-50:]

        self.save_session(
            member_id=member_id,
            slot_id=slot_id,
            session_key=session.session_key,
            thread_id=session.thread_id,
            conversation_history=session.conversation_history,
            adapter_state=session.adapter_state,
            status=session.status,
        )

    def delete_session(self, member_id: str, slot_id: str = "default") -> bool:
        """Delete a saved session."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM family_sessions WHERE member_id = ? AND slot_id = ?",
            (member_id, slot_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def list_active_sessions(self) -> list[dict]:
        """List all sessions with metadata (including non-active)."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT member_id, slot_id, thread_id, status,
                      json_array_length(conversation_history) as msg_count,
                      updated_at
               FROM family_sessions
               ORDER BY updated_at DESC"""
        ).fetchall()

        return [
            {
                "member_id": row["member_id"],
                "slot_id": row["slot_id"],
                "thread_id": row["thread_id"],
                "status": row["status"],
                "msg_count": row["msg_count"] or 0,
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def purge_old(self, max_age_days: int = 90) -> int:
        """Remove sessions older than max_age_days."""
        conn = self._get_conn()
        cutoff = time.time() - (max_age_days * 86400)
        cursor = conn.execute(
            "DELETE FROM family_sessions WHERE updated_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount

    # -- SessionProtocol implementation --

    def _require_state(self, session_id: str) -> tuple[SessionState, str, str]:
        member_id, slot_id = _parse_session_id(session_id)
        state = self.load_session(member_id, slot_id)
        if state is None:
            raise KeyError(f"Session not found: {session_id}")
        return state, member_id, slot_id

    def _set_status(self, member_id: str, slot_id: str, status: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE family_sessions SET status = ?, updated_at = ? WHERE member_id = ? AND slot_id = ?",
            (status, time.time(), member_id, slot_id),
        )
        conn.commit()

    def create(self, member_id: str, **kwargs: Any) -> SessionMetadata:
        slot_id = kwargs.get("slot_id", "default")
        session_key = kwargs.get("session_key", "")
        thread_id = kwargs.get("thread_id", "")
        conversation_history = kwargs.get("conversation_history")
        adapter_state = kwargs.get("adapter_state")

        self.save_session(
            member_id=member_id,
            slot_id=slot_id,
            session_key=session_key,
            thread_id=thread_id,
            conversation_history=conversation_history,
            adapter_state=adapter_state,
            status="active",
        )

        state = self._require_state(_make_session_id(member_id, slot_id))[0]
        return _state_to_metadata(state)

    def checkpoint(self, session_id: str, data: dict[str, Any]) -> SessionMetadata:
        state, member_id, slot_id = self._require_state(session_id)

        history = data.get("conversation_history", state.conversation_history)
        adapter_st = data.get("adapter_state", state.adapter_state)
        session_key = data.get("session_key", state.session_key)
        thread_id = data.get("thread_id", state.thread_id)

        self.save_session(
            member_id=member_id,
            slot_id=slot_id,
            session_key=session_key,
            thread_id=thread_id,
            conversation_history=history,
            adapter_state=adapter_st,
            status="checkpointed",
        )

        state = self._require_state(session_id)[0]
        return _state_to_metadata(state)

    def restore(self, session_id: str) -> dict[str, Any]:
        state, _, _ = self._require_state(session_id)
        return state.to_dict()

    def archive(self, session_id: str) -> SessionMetadata:
        state, member_id, slot_id = self._require_state(session_id)
        self._set_status(member_id, slot_id, "archived")
        state = self._require_state(session_id)[0]
        return _state_to_metadata(state)

    def expire(self, session_id: str) -> SessionMetadata:
        state, member_id, slot_id = self._require_state(session_id)
        metadata = _state_to_metadata(state, SessionStatus.EXPIRED)
        self.delete_session(member_id, slot_id)
        return metadata

    def get_metadata(self, session_id: str) -> SessionMetadata:
        state, _, _ = self._require_state(session_id)
        return _state_to_metadata(state)

    def list_sessions(
        self,
        member_id: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionMetadata]:
        raw = self.list_active_sessions()
        results: list[SessionMetadata] = []

        for entry in raw:
            mid = entry["member_id"]
            sid = entry["slot_id"]

            if member_id is not None and mid != member_id:
                continue

            session_status_str = entry.get("status", "active")
            if status is not None and session_status_str != status.value:
                continue

            state = self.load_session(mid, sid)
            if state is None:
                continue

            results.append(_state_to_metadata(state))

        return results

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# Singleton
_manager: FamilySessionManager | None = None


def get_session_manager() -> FamilySessionManager:
    """Get the global session manager singleton (auto-compression enabled)."""
    global _manager
    if _manager is None:
        _manager = FamilySessionManager(compression_config=DEFAULT_COMPRESSION_CONFIG)
    return _manager
