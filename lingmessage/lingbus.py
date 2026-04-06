from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from lingmessage.mailbox import Mailbox

_BUS_DIR = Path.home() / ".lingmessage"
_DB_NAME = "lingbus.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id     TEXT PRIMARY KEY,
    topic         TEXT NOT NULL,
    channel       TEXT NOT NULL DEFAULT 'ecosystem',
    status        TEXT NOT NULL DEFAULT 'active',
    participants  TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL UNIQUE,
    thread_id    TEXT NOT NULL,
    sender       TEXT NOT NULL,
    recipient    TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'reply',
    channel      TEXT NOT NULL DEFAULT 'ecosystem',
    subject      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    reply_to     TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    acked_by     TEXT NOT NULL DEFAULT '[]',
    source_type  TEXT NOT NULL DEFAULT 'inferred',
    source_trace TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (thread_id) REFERENCES threads(thread_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient);
CREATE INDEX IF NOT EXISTS idx_messages_rowid ON messages(rowid);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex[:16]


@dataclass
class BusMessage:
    rowid: int
    message_id: str
    thread_id: str
    sender: str
    recipient: str
    message_type: str
    channel: str
    subject: str
    body: str
    timestamp: str
    reply_to: str
    metadata: dict[str, str]
    acked_by: list[str]
    source_type: str
    source_trace: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> BusMessage:
        raw_meta = row["metadata"] or "{}"
        raw_acked = row["acked_by"] or "[]"
        return cls(
            rowid=row["rowid"],
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            sender=row["sender"],
            recipient=row["recipient"],
            message_type=row["message_type"],
            channel=row["channel"],
            subject=row["subject"],
            body=row["body"],
            timestamp=row["timestamp"],
            reply_to=row["reply_to"],
            metadata=json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta,
            acked_by=json.loads(raw_acked) if isinstance(raw_acked, str) else raw_acked,
            source_type=row["source_type"] if "source_type" in row.keys() else "inferred",
            source_trace=row["source_trace"] if "source_trace" in row.keys() else "",
        )


class LingBus:
    """LingBus v0.1 — SQLite WAL backed message bus.

    Experimental alternative backend to Mailbox. LingBus uses SQLite with WAL
    mode for concurrent read/write access. To bridge with the file-system
    Mailbox, use ``sync_from_mailbox()``.

    Status: experimental — API may change. Mailbox remains the primary backend.
    """

    def __init__(self, bus_dir: Path | None = None) -> None:
        self._dir = bus_dir or _BUS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / _DB_NAME
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def __enter__(self) -> LingBus:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def open_thread(
        self,
        topic: str,
        sender: str,
        recipients: list[str],
        channel: str = "ecosystem",
        subject: str = "",
        body: str = "",
    ) -> tuple[str, str]:
        thread_id = _new_id()
        message_id = _new_id()
        now = _now_iso()
        participants = list(set(recipients + [sender]))

        self._conn.execute(
            "INSERT INTO threads (thread_id, topic, channel, status, participants, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
            (thread_id, topic, channel, json.dumps(participants), now, now),
        )
        self._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp) VALUES (?, ?, ?, 'all', 'open', ?, ?, ?, ?)",
            (message_id, thread_id, sender, channel, subject or topic, body, now),
        )
        self._conn.execute(
            "UPDATE threads SET message_count = 1 WHERE thread_id = ?",
            (thread_id,),
        )
        self._conn.commit()
        return thread_id, message_id

    def post_reply(
        self,
        thread_id: str,
        sender: str,
        recipient: str,
        body: str,
        subject: str = "",
        message_type: str = "reply",
        metadata: dict[str, str] | None = None,
    ) -> str:
        message_id = _new_id()
        now = _now_iso()

        thread_row = self._conn.execute(
            "SELECT channel FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if thread_row is None:
            raise ValueError(f"Thread {thread_id} not found")
        channel = thread_row["channel"]

        self._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, thread_id, sender, recipient, message_type, channel, subject, body, now, json.dumps(metadata or {})),
        )
        self._conn.execute(
            "UPDATE threads SET message_count = message_count + 1, updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )

        sender_in = self._conn.execute(
            "SELECT participants FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if sender_in:
            parts = json.loads(sender_in["participants"])
            if sender not in parts:
                parts.append(sender)
                self._conn.execute(
                    "UPDATE threads SET participants = ? WHERE thread_id = ?",
                    (json.dumps(parts), thread_id),
                )
        self._conn.commit()
        return message_id

    def poll(self, recipient: str, since_rowid: int = 0, limit: int = 50) -> list[BusMessage]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') ORDER BY rowid ASC LIMIT ?",
            (since_rowid, recipient, limit),
        ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    def get_thread(self, thread_id: str) -> list[BusMessage]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY rowid ASC",
            (thread_id,),
        ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    def list_threads(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM threads WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM threads ORDER BY updated_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "thread_id": r["thread_id"],
                "topic": r["topic"],
                "channel": r["channel"],
                "status": r["status"],
                "participants": json.loads(r["participants"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": r["message_count"],
            })
        return result

    def ack(self, message_id: str, member: str) -> bool:
        row = self._conn.execute(
            "SELECT acked_by FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return False
        acked = json.loads(row["acked_by"])
        if member not in acked:
            acked.append(member)
            self._conn.execute(
                "UPDATE messages SET acked_by = ? WHERE message_id = ?",
                (json.dumps(acked), message_id),
            )
            self._conn.commit()
        return True

    def get_max_rowid(self, recipient: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(rowid) as max_id FROM messages WHERE recipient = ? OR recipient = 'all'",
            (recipient,),
        ).fetchone()
        return row["max_id"] or 0

    def stats(self) -> dict[str, Any]:
        threads = self._conn.execute("SELECT COUNT(*) as c FROM threads").fetchone()["c"]
        messages = self._conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        unacked = self._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE acked_by = '[]'"
        ).fetchone()["c"]
        return {"threads": threads, "messages": messages, "unacked": unacked}

    def sync_from_mailbox(self, mailbox: Mailbox) -> int:
        """Import all threads from a Mailbox instance into LingBus.

        Returns the number of threads imported. Skips threads whose
        ``thread_id`` already exists in LingBus (idempotent).
        """
        imported = 0
        for header in mailbox.list_threads():
            existing = self._conn.execute(
                "SELECT 1 FROM threads WHERE thread_id = ?",
                (header.thread_id,),
            ).fetchone()
            if existing:
                continue

            participants = list(header.participants)
            self._conn.execute(
                "INSERT INTO threads (thread_id, topic, channel, status, participants, created_at, updated_at, message_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    header.thread_id,
                    header.topic,
                    header.channel.value,
                    header.status.value,
                    json.dumps(participants),
                    header.created_at,
                    header.updated_at or _now_iso(),
                    header.message_count,
                ),
            )

            messages = mailbox.load_thread_messages(header.thread_id)
            for m in messages:
                self._conn.execute(
                    "INSERT OR IGNORE INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp, reply_to, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        m.message_id,
                        m.thread_id,
                        m.sender.value if hasattr(m.sender, "value") else str(m.sender),
                        m.recipient.value if hasattr(m.recipient, "value") else str(m.recipient),
                        m.message_type.value if hasattr(m.message_type, "value") else str(m.message_type),
                        m.channel.value if hasattr(m.channel, "value") else str(m.channel),
                        m.subject,
                        m.body,
                        m.timestamp,
                        m.reply_to or "",
                        json.dumps(dict(m.metadata)) if m.metadata else "{}",
                    ),
                )
            self._conn.commit()
            imported += 1
        return imported
