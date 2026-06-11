"""Family Offline Queue — Persistent message queue for offline members.

When a family member is offline, messages are enqueued.
When the member comes back online, queued messages are auto-synced.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("lingmessage.offline_queue")

DEFAULT_DB_PATH = Path.home() / ".lingmessage" / "offline_queue.db"


@dataclass
class QueuedMessage:
    """A message waiting to be delivered to an offline member."""
    id: int = 0
    member_id: str = ""
    thread_id: str = ""
    sender: str = ""
    body: str = ""
    enqueued_at: float = field(default_factory=time.time)
    retry_count: int = 0
    last_retry_at: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "member_id": self.member_id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "body": self.body,
            "enqueued_at": self.enqueued_at,
            "retry_count": self.retry_count,
            "last_retry_at": self.last_retry_at,
            "metadata": self.metadata,
        }


class FamilyOfflineQueue:
    """SQLite-backed offline message queue.

    Stores messages destined for offline members.
    When a member comes online, call sync_for_member() to deliver
    all pending messages.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
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
            CREATE TABLE IF NOT EXISTS offline_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                enqueued_at REAL NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_at REAL NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                delivered INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_offline_member
            ON offline_queue(member_id, delivered)
        """)
        conn.commit()

    def enqueue(
        self,
        member_id: str,
        thread_id: str,
        sender: str,
        body: str,
        metadata: dict | None = None,
    ) -> int:
        """Enqueue a message for an offline member.

        Returns the queue entry ID.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO offline_queue
               (member_id, thread_id, sender, body, enqueued_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                member_id,
                thread_id,
                sender,
                body,
                time.time(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        msg_id = cursor.lastrowid or 0
        logger.info(f"Enqueued message {msg_id} for offline member {member_id}")
        return msg_id

    def dequeue(self, member_id: str, limit: int = 50) -> list[QueuedMessage]:
        """Get and mark as delivered the pending messages for a member.

        Returns list of QueuedMessage that were delivered.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, member_id, thread_id, sender, body,
                      enqueued_at, retry_count, last_retry_at, metadata
               FROM offline_queue
               WHERE member_id = ? AND delivered = 0
               ORDER BY enqueued_at ASC
               LIMIT ?""",
            (member_id, limit),
        ).fetchall()

        if not rows:
            return []

        ids = [row["id"] for row in rows]
        now = time.time()
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE offline_queue SET delivered = 1, delivered_at = ? WHERE id IN ({placeholders})",
            [now] + ids,
        )
        conn.commit()

        messages = []
        for row in rows:
            messages.append(QueuedMessage(
                id=row["id"],
                member_id=row["member_id"],
                thread_id=row["thread_id"],
                sender=row["sender"],
                body=row["body"],
                enqueued_at=row["enqueued_at"],
                retry_count=row["retry_count"],
                last_retry_at=row["last_retry_at"],
                metadata=json.loads(row["metadata"]),
            ))

        logger.info(f"Dequeued {len(messages)} messages for {member_id}")
        return messages

    def peek(self, member_id: str, limit: int = 50) -> list[QueuedMessage]:
        """Peek at pending messages without marking them as delivered."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, member_id, thread_id, sender, body,
                      enqueued_at, retry_count, last_retry_at, metadata
               FROM offline_queue
               WHERE member_id = ? AND delivered = 0
               ORDER BY enqueued_at ASC
               LIMIT ?""",
            (member_id, limit),
        ).fetchall()

        return [
            QueuedMessage(
                id=row["id"],
                member_id=row["member_id"],
                thread_id=row["thread_id"],
                sender=row["sender"],
                body=row["body"],
                enqueued_at=row["enqueued_at"],
                retry_count=row["retry_count"],
                last_retry_at=row["last_retry_at"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def get_pending_count(self, member_id: str) -> int:
        """Get the count of pending messages for a member."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM offline_queue WHERE member_id = ? AND delivered = 0",
            (member_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_all_pending_counts(self) -> dict[str, int]:
        """Get pending message counts for all members."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT member_id, COUNT(*) as cnt FROM offline_queue WHERE delivered = 0 GROUP BY member_id"
        ).fetchall()
        return {row["member_id"]: row["cnt"] for row in rows}

    def increment_retry(self, msg_id: int) -> None:
        """Increment retry count for a message."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE offline_queue SET retry_count = retry_count + 1, last_retry_at = ? WHERE id = ?",
            (time.time(), msg_id),
        )
        conn.commit()

    def purge_old(self, max_age_days: int = 30) -> int:
        """Remove old delivered messages."""
        conn = self._get_conn()
        cutoff = time.time() - (max_age_days * 86400)
        cursor = conn.execute(
            "DELETE FROM offline_queue WHERE delivered = 1 AND delivered_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# Singleton
_queue: FamilyOfflineQueue | None = None


def get_offline_queue() -> FamilyOfflineQueue:
    """Get the global offline queue singleton."""
    global _queue
    if _queue is None:
        _queue = FamilyOfflineQueue()
    return _queue
