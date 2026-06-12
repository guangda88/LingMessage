from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from lingmessage.types import Channel, LingIdentity

_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 0.1
_RETRY_MAX_DELAY = 2.0

_WRITE_RLOCK = threading.RLock()

def _serialized_write(func):
    """Decorator: serialize write operations within a process (RLock).
    Cross-process safety is handled by SQLite WAL + busy_timeout.
    """
    def wrapper(self, *args, **kwargs):
        with _WRITE_RLOCK:
            return func(self, *args, **kwargs)
    return wrapper

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lingmessage.mailbox import Mailbox

_VALID_SENDERS: set[str] = {e.value for e in LingIdentity if e.value != "all"}
_SYSTEM_SENDERS: set[str] = {
    "session_rotation_monitor",
    "h7_dedup_monitor",
    "task_drift_detector",
    "webui_user",
}
_ALL_VALID_SENDERS: set[str] = _VALID_SENDERS | _SYSTEM_SENDERS
_VALID_CHANNELS: set[str] = {e.value for e in Channel}

_BUS_DIR = Path(os.environ.get("LINGBUS_DB_PATH", str(Path.home() / ".lingmessage")))
_DB_NAME = "lingbus.db"

_THROTTLE_WINDOW = 300        # seconds — dedup window for identical messages
_THROTTLE_MIN_INTERVAL = 30   # seconds — minimum between messages from same sender in same thread
_THROTTLE_MAX_BURST = 5       # max messages from same sender within _THROTTLE_WINDOW
_THROTTLE_DAILY_THREAD_LIMIT = 200  # max messages from same sender to same thread per day
_THROTTLE_DAILY_SENDER_LIMIT = 500   # max messages from same sender across all threads per day (SDTH defense)
_THROTTLE_NEW_THREAD = "__new_thread__"  # sentinel thread_id for open_thread dedup

_URGENT_SENDERS = {"webui_user", "user"}  # senders whose messages get urgent priority

_ALERT_DEDUP_CHANNELS = {"alert", "system"}  # channels with subject-based dedup
_ALERT_DEDUP_WINDOW = 600  # seconds — dedup window for same subject in alert/system

_LARGE_MSG_THRESHOLD = 51200  # 50KB — thinking bloat / oversized message detection
_LARGE_MSG_DEDUP_WINDOW = 600  # seconds — dedup per sender

_DELIVERY_TIMEOUT_SECONDS = 300  # 5 min before first retry
_MAX_DELIVERY_ATTEMPTS = 3
_DELIVERY_BACKOFF_BASE = 60  # seconds, exponential backoff

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
CREATE INDEX IF NOT EXISTS idx_messages_sender_ts ON messages(sender, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel);

CREATE TABLE IF NOT EXISTS rate_limits (
    sender       TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    body_hash    TEXT NOT NULL,
    timestamp    REAL NOT NULL,
    PRIMARY KEY (sender, thread_id, body_hash)
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    message_id     TEXT NOT NULL,
    recipient      TEXT NOT NULL,
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    next_retry_at  TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (message_id) REFERENCES messages(message_id),
    PRIMARY KEY (message_id, recipient)
);
CREATE INDEX IF NOT EXISTS idx_delivery_status ON delivery_attempts(status);

CREATE TABLE IF NOT EXISTS pending_for (
    message_id     TEXT NOT NULL,
    recipient      TEXT NOT NULL,
    queued_at      TEXT NOT NULL,
    acked          INTEGER NOT NULL DEFAULT 0,
    acked_at       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (message_id, recipient)
);
CREATE INDEX IF NOT EXISTS idx_pending_for_recipient ON pending_for(recipient, acked);

CREATE TABLE IF NOT EXISTS write_auth_log (
    id           TEXT PRIMARY KEY,
    caller       TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    intent       TEXT NOT NULL DEFAULT '',
    auth_source  TEXT NOT NULL,
    auth_thread_id TEXT NOT NULL DEFAULT '',
    timestamp    TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_write_auth_caller ON write_auth_log(caller, expires_at);
"""

def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a unique ID using full UUID (128-bit)."""
    return uuid4().hex


def _retry_at_iso(base: str, attempt: int) -> str:
    dt = datetime.fromisoformat(base)
    delay = _DELIVERY_BACKOFF_BASE * (2 ** attempt)
    return (dt + timedelta(seconds=delay)).isoformat()


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

    def __init__(self, bus_dir: Path | None = None, *, throttle: bool = True) -> None:
        self._dir = bus_dir or _BUS_DIR
        self._throttle_enabled = throttle
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / _DB_NAME
        self._conn = None
        try:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA_SQL)
        except Exception:
            if self._conn:
                self._conn.close()
                self._conn = None
            raise

    def __enter__(self) -> LingBus:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Safely close the database connection if it exists."""
        if self._conn:
            self._checkpoint()  # WAL checkpoint before close
            self._conn.close()
            self._conn = None

    @staticmethod
    def _is_lock_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "locked" in msg or "busy" in msg

    def _safe_commit(self) -> None:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                self._conn.commit()
                return
            except sqlite3.OperationalError as e:
                if not self._is_lock_error(e) or attempt == _RETRY_ATTEMPTS - 1:
                    raise
                delay = min(_RETRY_BASE_DELAY * (2 ** attempt), _RETRY_MAX_DELAY)
                logger.debug("_safe_commit retry %d/%d after %.2fs", attempt + 1, _RETRY_ATTEMPTS, delay)
                time.sleep(delay)

    def _safe_execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return self._conn.execute(sql, params)
            except sqlite3.OperationalError as e:
                if not self._is_lock_error(e) or attempt == _RETRY_ATTEMPTS - 1:
                    raise
                delay = min(_RETRY_BASE_DELAY * (2 ** attempt), _RETRY_MAX_DELAY)
                logger.debug("_safe_execute retry %d/%d after %.2fs", attempt + 1, _RETRY_ATTEMPTS, delay)
                time.sleep(delay)

    def _checkpoint(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    _NOTIFY_FLAG = Path.home() / ".lingmessage" / ".new_msg"

    def _touch_notify(self) -> None:
        try:
            self._NOTIFY_FLAG.touch()
        except Exception:
            pass

    @classmethod
    def get_notify_flag_path(cls) -> Path:
        return cls._NOTIFY_FLAG

    def _broadcast_msg(self, msg_dict: dict[str, Any]) -> None:
        """Fire-and-forget notify to SSE push server. Never raises."""
        try:
            from lingmessage.push_manager import notify_push_server
            notify_push_server(msg_dict)
        except Exception:
            pass

    def _broadcast_open(self, thread_id: str, message_id: str, sender: str,
                        channel: str, subject: str, body: str, timestamp: str) -> None:
        self._broadcast_msg({
            "type": "open_thread",
            "thread_id": thread_id,
            "message_id": message_id,
            "sender": sender,
            "recipient": "all",
            "channel": channel,
            "subject": subject,
            "body": body,
            "timestamp": timestamp,
        })

    def _broadcast_reply(self, message_id: str, thread_id: str, sender: str,
                         recipient: str, channel: str, subject: str, body: str,
                         timestamp: str) -> None:
        self._broadcast_msg({
            "type": "reply",
            "message_id": message_id,
            "thread_id": thread_id,
            "sender": sender,
            "recipient": recipient,
            "channel": channel,
            "subject": subject,
            "body": body,
            "timestamp": timestamp,
        })

    @_serialized_write
    def checkpoint_wal(self, mode: str = "FULL") -> None:
        """Run explicit WAL checkpoint to control file size.

        Args:
            mode: PASSIVE (don't wait), FULL, or RESTART.
        """
        mode_upper = mode.upper()
        if mode_upper not in ("PASSIVE", "FULL", "RESTART"):
            raise ValueError(f"invalid checkpoint mode: {mode}")
        self._conn.execute(f"PRAGMA wal_checkpoint({mode_upper})")

    @_serialized_write
    def prune_rate_limits(self, *, older_than_hours: int = 25) -> int:
        """Remove rate_limit entries older than the given hours.

        Since rate_limit window is 5 minutes, entries older than 25h are
        definitely safe to remove. Returns number of rows deleted.
        """
        cutoff = time.time() - (older_than_hours * 3600)
        cursor = self._conn.execute(
            "DELETE FROM rate_limits WHERE timestamp < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    @_serialized_write
    def prune_delivery_attempts(self, *, older_than_days: int = 7) -> int:
        """Remove confirmed/handled delivery_attempts older than N days.

        Only escalated entries should stay longer for audit trail.
        Returns number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM delivery_attempts "
            "WHERE status IN ('confirmed', 'handled') AND last_attempt_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def execute_readonly(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """只读查询接口 — 只允许 SELECT/PRAGMA."""
        normalized = sql.strip().upper()
        if not (normalized.startswith("SELECT") or normalized.startswith("PRAGMA")):
            raise ValueError(f"execute_readonly only allows SELECT/PRAGMA, got: {sql[:40]}")
        return self._conn.execute(sql, params).fetchall()

    @_serialized_write
    def execute_write(self, sql: str, params: tuple = ()) -> None:
        """受控写接口 — 禁止 DROP/ALTER/CREATE/ATTACH/DETACH."""
        normalized = sql.strip().upper()
        for kw in ("DROP", "ALTER", "CREATE", "ATTACH", "DETACH"):
            if normalized.startswith(kw):
                raise ValueError(f"execute_write forbidden: {kw}")
        self._conn.execute(sql, params)
        self._conn.commit()

    @_serialized_write
    def ensure_table(self, create_sql: str) -> None:
        """受控建表 — 只允许 CREATE TABLE IF NOT EXISTS."""
        if "CREATE TABLE IF NOT EXISTS" not in create_sql.upper():
            raise ValueError("ensure_table only allows CREATE TABLE IF NOT EXISTS")
        self._conn.execute(create_sql)
        self._conn.commit()

    @_serialized_write
    def vacuum(self) -> None:
        """Defragment the database and reclaim free space."""
        self._conn.execute("VACUUM")

    def _check_throttle(self, sender: str, thread_id: str, body: str) -> str | None:
        """Check if a message should be throttled. Returns reason or None."""
        if not self._throttle_enabled:
            return None
        now = time.time()
        bhash = _body_hash(body)

        # 1) Dedup: identical body within window
        row = self._conn.execute(
            "SELECT timestamp FROM rate_limits WHERE sender=? AND thread_id=? AND body_hash=?",
            (sender, thread_id, bhash),
        ).fetchone()
        if row and (now - row["timestamp"]) < _THROTTLE_WINDOW:
            logger.warning("throttle: dedup sender=%s thread=%s hash=%s", sender, thread_id, bhash)
            return f"duplicate: same content within {_THROTTLE_WINDOW}s"

        # 2) Burst: too many messages in window
        cutoff = now - _THROTTLE_WINDOW
        recent = self._conn.execute(
            "SELECT COUNT(*) as c FROM rate_limits WHERE sender=? AND thread_id=? AND timestamp>?",
            (sender, thread_id, cutoff),
        ).fetchone()["c"]
        if recent >= _THROTTLE_MAX_BURST:
            logger.warning("throttle: burst sender=%s thread=%s count=%d", sender, thread_id, recent)
            return f"burst: {recent} messages within {_THROTTLE_WINDOW}s (max {_THROTTLE_MAX_BURST})"

        # 3) Min interval
        last = self._conn.execute(
            "SELECT MAX(timestamp) as t FROM rate_limits WHERE sender=? AND thread_id=?",
            (sender, thread_id),
        ).fetchone()["t"]
        if last and (now - last) < _THROTTLE_MIN_INTERVAL:
            logger.warning("throttle: min_interval sender=%s thread=%s", sender, thread_id)
            return f"rate: min interval {_THROTTLE_MIN_INTERVAL}s"

        # 4) Daily thread limit
        day_ago = now - 86400
        daily_count = self._conn.execute(
            "SELECT COUNT(*) as c FROM rate_limits WHERE sender=? AND thread_id=? AND timestamp>?",
            (sender, thread_id, day_ago),
        ).fetchone()["c"]
        if daily_count >= _THROTTLE_DAILY_THREAD_LIMIT:
            logger.warning("throttle: daily_limit sender=%s thread=%s count=%d", sender, thread_id, daily_count)
            return f"daily_limit: {daily_count} messages to this thread today (max {_THROTTLE_DAILY_THREAD_LIMIT})"

        # 5) Daily sender limit (SDTH defense — session hard cap)
        daily_sender_count = self._conn.execute(
            "SELECT COUNT(*) as c FROM rate_limits WHERE sender=? AND timestamp>?",
            (sender, day_ago),
        ).fetchone()["c"]
        if daily_sender_count >= _THROTTLE_DAILY_SENDER_LIMIT:
            logger.warning("throttle: daily_sender_limit sender=%s count=%d", sender, daily_sender_count)
            return f"daily_sender_limit: {daily_sender_count} messages today (max {_THROTTLE_DAILY_SENDER_LIMIT})"

        # Record this message
        self._conn.execute(
            "INSERT OR REPLACE INTO rate_limits (sender, thread_id, body_hash, timestamp) VALUES (?,?,?,?)",
            (sender, thread_id, bhash, now),
        )
        self._conn.commit()
        return None

    def _check_alert_subject_dedup(self, sender: str, channel: str, subject: str) -> str | None:
        """Dedup identical-subject messages in alert/system channels.

        Health-patrol sends recurring alerts as new threads every ~5min with
        near-identical subjects but differing body timestamps. The standard
        body-hash dedup in _check_throttle cannot catch these because each
        new thread has a fresh body. This method blocks repeated alert
        subjects within a longer window, preventing alert storms from
        flooding the bus.
        """
        if channel not in _ALERT_DEDUP_CHANNELS or not subject:
            return None
        if not self._throttle_enabled:
            return None
        now = time.time()
        shash = _body_hash(subject)
        row = self._conn.execute(
            "SELECT timestamp FROM rate_limits WHERE sender=? AND thread_id=? AND body_hash=?",
            (sender, f"__alert_subject__:{channel}", shash),
        ).fetchone()
        if row and (now - row["timestamp"]) < _ALERT_DEDUP_WINDOW:
            logger.warning(
                "alert_dedup: sender=%s channel=%s subject_hash=%s",
                sender, channel, shash,
            )
            return f"alert_dedup: same subject within {_ALERT_DEDUP_WINDOW}s"
        self._conn.execute(
            "INSERT OR REPLACE INTO rate_limits (sender, thread_id, body_hash, timestamp) VALUES (?,?,?,?)",
            (sender, f"__alert_subject__:{channel}", shash, now),
        )
        self._conn.commit()
        return None

    def _alert_large_message(self, sender: str, body_size: int) -> None:
        """Emit an alert if a message body exceeds the large-message threshold.

        Detects thinking-bloat / oversized messages without blocking them.
        Uses per-sender dedup so a member sending consecutive large messages
        only triggers one alert within the dedup window.
        """
        if body_size < _LARGE_MSG_THRESHOLD:
            return
        if not self._throttle_enabled:
            return
        now = time.time()
        row = self._conn.execute(
            "SELECT timestamp FROM rate_limits WHERE sender=? AND thread_id=? AND body_hash=?",
            ("lingmessage", f"__large_msg__:{sender}", "_"),
        ).fetchone()
        if row and (now - row["timestamp"]) < _LARGE_MSG_DEDUP_WINDOW:
            logger.warning(
                "large_msg_dedup: sender=%s size=%d (suppressed)", sender, body_size,
            )
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO rate_limits (sender, thread_id, body_hash, timestamp) VALUES (?,?,?,?)",
            ("lingmessage", f"__large_msg__:{sender}", "_", now),
        )
        self._conn.commit()
        size_kb = body_size // 1024
        logger.warning("large_msg_alert: sender=%s size=%dKB", sender, size_kb)
        alert_thread_id = _new_id()
        alert_msg_id = _new_id()
        ts = _now_iso()
        alert_topic = f"⚠️ 大消息告警: {sender} {size_kb}KB"
        alert_body = (
            f"## 大消息告警\n\n"
            f"- **发送者**: {sender}\n"
            f"- **大小**: {size_kb}KB ({body_size} bytes)\n"
            f"- **时间**: {ts}\n"
            f"- **阈值**: {_LARGE_MSG_THRESHOLD // 1024}KB\n\n"
            f"该消息已正常入库（未拦截）。可能是thinking膨胀或超大内容，请相关成员检查。\n"
            f"— 灵信 (lingmessage) 自动巡检"
        )
        self._conn.execute(
            "INSERT INTO threads (thread_id, topic, channel, status, participants, created_at, updated_at, message_count) "
            "VALUES (?, ?, 'alert', 'active', '[\"all\"]', ?, ?, 1)",
            (alert_thread_id, alert_topic, ts, ts),
        )
        self._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp) "
            "VALUES (?, ?, 'lingmessage', 'all', 'open', 'alert', ?, ?, ?)",
            (alert_msg_id, alert_thread_id, alert_topic, alert_body, ts),
        )
        self._conn.commit()

    def _validate_sender(self, sender: str) -> None:
        """Reject messages from unregistered senders."""
        if sender not in _ALL_VALID_SENDERS:
            raise ValueError(f"unknown sender: {sender!r} (valid: {sorted(_VALID_SENDERS)})")

    def _sign_if_key(self, message_id: str, sender: str, content: str, timestamp: str) -> str:
        """Sign message if LINGMESSAGE_SIGNING_KEY is set in environment."""
        key = os.environ.get("LINGMESSAGE_SIGNING_KEY", "")
        if not key:
            return ""
        payload = f"{message_id}:{sender}:{content}:{timestamp}"
        sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"sig:{sig}"

    @_serialized_write
    def open_thread(
        self,
        topic: str,
        sender: str,
        recipients: list[str],
        channel: str = "ecosystem",
        subject: str = "",
        body: str = "",
    ) -> tuple[str, str]:
        self._validate_sender(sender)
        if channel not in _VALID_CHANNELS:
            raise ValueError(f"invalid channel: {channel!r} (valid: {sorted(_VALID_CHANNELS)})")
        thread_id = _new_id()
        alert_dedup_reason = self._check_alert_subject_dedup(sender, channel, subject or topic)
        if alert_dedup_reason:
            raise ValueError(f"throttled: {alert_dedup_reason}")
        throttle_reason = self._check_throttle(sender, _THROTTLE_NEW_THREAD, body or topic)
        if throttle_reason:
            raise ValueError(f"throttled: {throttle_reason}")
        message_id = _new_id()
        now = _now_iso()
        participants = list(set(recipients + [sender]))
        source_trace = self._sign_if_key(message_id, sender, body or subject or topic, now)

        self._conn.execute(
            "INSERT INTO threads (thread_id, topic, channel, status, participants, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
            (thread_id, topic, channel, json.dumps(participants), now, now),
        )
        self._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp, source_trace) VALUES (?, ?, ?, 'all', 'open', ?, ?, ?, ?, ?)",
            (message_id, thread_id, sender, channel, subject or topic, body, now, source_trace),
        )
        self._conn.execute(
            "UPDATE threads SET message_count = 1 WHERE thread_id = ?",
            (thread_id,),
        )
        self._conn.commit()
        self.queue_pending(message_id, recipients)
        self._touch_notify()
        self._broadcast_open(thread_id, message_id, sender, channel, subject or topic, body, now)
        self._alert_large_message(sender, len(body))
        return thread_id, message_id

    @_serialized_write
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
        self._validate_sender(sender)
        message_id = _new_id()
        now = _now_iso()

        throttle_reason = self._check_throttle(sender, thread_id, body)
        if throttle_reason:
            raise ValueError(f"throttled: {throttle_reason}")

        thread_row = self._conn.execute(
            "SELECT channel FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if thread_row is None:
            raise ValueError(f"Thread {thread_id} not found")
        channel = thread_row["channel"]

        source_trace = self._sign_if_key(message_id, sender, body, now)
        self._conn.execute(
            "INSERT INTO messages (message_id, thread_id, sender, recipient, message_type, channel, subject, body, timestamp, metadata, source_trace) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, thread_id, sender, recipient, message_type, channel, subject, body, now, json.dumps(metadata or {}), source_trace),
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
        if recipient != "all":
            self.queue_pending(message_id, [recipient])
        else:
            thread_parts = self._conn.execute(
                "SELECT participants FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread_parts:
                others = [p for p in json.loads(thread_parts["participants"]) if p != sender]
                self.queue_pending(message_id, others)
        self._touch_notify()
        self._broadcast_reply(message_id, thread_id, sender, recipient, channel, subject, body, now)
        self._alert_large_message(sender, len(body))
        return message_id

    def poll(self, recipient: str, since_rowid: int = 0, limit: int = 50, caller: str | None = None, reverse: bool = True, channels: list[str] | None = None) -> list[BusMessage]:
        order_clause = "DESC" if reverse else "ASC"
        if channels:
            placeholders = ",".join("?" for _ in channels)
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') AND channel IN ({placeholders}) ORDER BY "
                f"CASE WHEN sender IN ({','.join('?' for _ in _URGENT_SENDERS)}) THEN 0 ELSE 1 END, rowid "
                + order_clause + " LIMIT ?",
                (since_rowid, recipient, *channels, *_URGENT_SENDERS, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') ORDER BY "
                "CASE WHEN sender IN (" + ",".join("?" for _ in _URGENT_SENDERS) + ") THEN 0 ELSE 1 END, rowid "
                + order_clause + " LIMIT ?",
                (since_rowid, recipient, *_URGENT_SENDERS, limit),
            ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    def poll_urgent(self, recipient: str, since_rowid: int = 0) -> list[BusMessage]:
        """Poll only urgent (user-originated) messages for a recipient.

        Urgent messages are those from senders in _URGENT_SENDERS (webui_user, user).
        Returns messages ordered newest-first, limited to 50.
        """
        if not _URGENT_SENDERS:
            return []
        placeholders = ",".join("?" for _ in _URGENT_SENDERS)
        rows = self._conn.execute(
            f"SELECT * FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') "
            f"AND sender IN ({placeholders}) ORDER BY rowid DESC LIMIT 50",
            (since_rowid, recipient, *_URGENT_SENDERS),
        ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    def get_thread(self, thread_id: str, caller: str | None = None, reverse: bool = True) -> list[BusMessage]:
        if caller is not None:
            thread_row = self._conn.execute(
                "SELECT participants FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread_row is None:
                return []
            participants = json.loads(thread_row["participants"])
            if caller not in participants:
                return []
        order_clause = "DESC" if reverse else "ASC"
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY rowid " + order_clause,
            (thread_id,),
        ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    def list_threads(self, status: str | None = None, caller: str | None = None) -> list[dict[str, Any]]:
        if caller is not None:
            rows = self._conn.execute(
                "SELECT * FROM threads WHERE participants LIKE ? ORDER BY updated_at DESC",
                (f'%"{caller}"%',),
            ).fetchall()
        elif status:
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
            last_sender_row = self._conn.execute(
                "SELECT sender FROM messages WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1",
                (r["thread_id"],),
            ).fetchone()
            result.append({
                "thread_id": r["thread_id"],
                "topic": r["topic"],
                "channel": r["channel"],
                "status": r["status"],
                "participants": json.loads(r["participants"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": r["message_count"],
                "last_sender": last_sender_row["sender"] if last_sender_row else None,
            })
        return result

    @_serialized_write
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
            self._safe_commit()
        return True

    def get_max_rowid(self, recipient: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(rowid) as max_id FROM messages WHERE recipient = ? OR recipient = 'all'",
            (recipient,),
        ).fetchone()
        return row["max_id"] or 0

    def get_global_max_rowid(self) -> int:
        """Return the highest rowid across all messages (no recipient filter)."""
        row = self._conn.execute("SELECT MAX(rowid) as max_id FROM messages").fetchone()
        return row["max_id"] or 0

    def watch_changes(self, since_rowid: int, limit: int = 100) -> list[BusMessage]:
        """Return messages with rowid > since_rowid, ordered oldest first.

        Designed for push-style watchers: caller tracks last seen rowid and
        calls this method to fetch incremental changes.
        """
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
            (since_rowid, limit),
        ).fetchall()
        return [BusMessage.from_row(r) for r in rows]

    @_serialized_write
    def confirm_delivery(self, message_id: str, recipient: str) -> dict[str, Any]:
        if recipient == "all":
            return {"status": "skipped", "reason": "broadcast"}

        msg_row = self._conn.execute(
            "SELECT acked_by FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if msg_row is None:
            return {"status": "skipped", "reason": "message_not_found"}

        now = _now_iso()
        acked_by: list[str] = json.loads(msg_row["acked_by"])

        existing = self._conn.execute(
            "SELECT * FROM delivery_attempts WHERE message_id = ? AND recipient = ?",
            (message_id, recipient),
        ).fetchone()

        if recipient in acked_by:
            status = "confirmed"
            self._conn.execute(
                "INSERT OR REPLACE INTO delivery_attempts "
                "(message_id, recipient, attempt_count, last_attempt_at, status, next_retry_at, created_at) "
                "VALUES (?, ?, COALESCE((SELECT attempt_count FROM delivery_attempts WHERE message_id=? AND recipient=?), 0), ?, ?, ?, "
                "COALESCE((SELECT created_at FROM delivery_attempts WHERE message_id=? AND recipient=?), ?))",
                (message_id, recipient, message_id, recipient, now, status, "", message_id, recipient, now),
            )
            self._safe_commit()
            return {"status": "confirmed", "attempt_count": existing["attempt_count"] if existing else 0}

        if existing is None:
            self._conn.execute(
                "INSERT INTO delivery_attempts (message_id, recipient, attempt_count, last_attempt_at, status, next_retry_at, created_at) "
                "VALUES (?, ?, 1, ?, 'pending', ?, ?)",
                (message_id, recipient, now, _retry_at_iso(now, 1), now),
            )
            self._safe_commit()
            return {"status": "pending", "attempt_count": 1, "next_retry_at": _retry_at_iso(now, 1)}

        attempt_count = existing["attempt_count"]
        cur_status = existing["status"]
        if cur_status in ("confirmed", "escalated", "handled"):
            return {"status": cur_status, "attempt_count": attempt_count}

        attempt_count += 1
        if attempt_count >= _MAX_DELIVERY_ATTEMPTS:
            status = "escalated"
            next_retry = ""
        else:
            status = "pending"
            next_retry = _retry_at_iso(now, attempt_count)

        self._conn.execute(
            "UPDATE delivery_attempts SET attempt_count=?, last_attempt_at=?, status=?, next_retry_at=? "
            "WHERE message_id=? AND recipient=?",
            (attempt_count, now, status, next_retry, message_id, recipient),
        )
        self._conn.commit()
        return {"status": status, "attempt_count": attempt_count, "next_retry_at": next_retry}

    def get_delivery_status(self, message_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM delivery_attempts WHERE message_id = ?", (message_id,)
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "message_id": r["message_id"],
                "recipient": r["recipient"],
                "attempt_count": r["attempt_count"],
                "last_attempt_at": r["last_attempt_at"],
                "status": r["status"],
                "next_retry_at": r["next_retry_at"],
                "created_at": r["created_at"],
            })
        return result

    def pending_deliveries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        now = _now_iso()
        rows = self._conn.execute(
            "SELECT da.message_id, da.recipient, da.attempt_count, da.status, "
            "da.next_retry_at, m.sender, m.thread_id, m.body "
            "FROM delivery_attempts da JOIN messages m ON da.message_id = m.message_id "
            "WHERE da.status = 'pending' AND (da.next_retry_at = '' OR da.next_retry_at <= ?) "
            "LIMIT ?",
            (now, limit),
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "message_id": r["message_id"],
                "recipient": r["recipient"],
                "attempt_count": r["attempt_count"],
                "status": r["status"],
                "next_retry_at": r["next_retry_at"],
                "sender": r["sender"],
                "thread_id": r["thread_id"],
                "body": r["body"],
            })
        return result

    def escalated_deliveries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT da.message_id, da.recipient, da.attempt_count, da.status, "
            "da.last_attempt_at, da.created_at, m.sender, m.thread_id, m.body "
            "FROM delivery_attempts da JOIN messages m ON da.message_id = m.message_id "
            "WHERE da.status = 'escalated' "
            "ORDER BY da.last_attempt_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "message_id": r["message_id"],
                "recipient": r["recipient"],
                "attempt_count": r["attempt_count"],
                "status": r["status"],
                "last_attempt_at": r["last_attempt_at"],
                "created_at": r["created_at"],
                "sender": r["sender"],
                "thread_id": r["thread_id"],
                "body": r["body"],
            })
        return result

    @_serialized_write
    def mark_escalation_handled(self, message_id: str, recipient: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE delivery_attempts SET status = 'handled', next_retry_at = '' "
            "WHERE message_id = ? AND recipient = ? AND status = 'escalated'",
            (message_id, recipient),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def stats(self) -> dict[str, Any]:
        threads = self._conn.execute("SELECT COUNT(*) as c FROM threads").fetchone()["c"]
        messages = self._conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        unacked = self._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE acked_by = '[]'"
        ).fetchone()["c"]
        return {
            "threads": threads,
            "messages": messages,
            "unacked": unacked,
            "delivery_pending": self._conn.execute(
                "SELECT COUNT(*) as c FROM delivery_attempts WHERE status='pending'"
            ).fetchone()["c"],
            "delivery_confirmed": self._conn.execute(
                "SELECT COUNT(*) as c FROM delivery_attempts WHERE status='confirmed'"
            ).fetchone()["c"],
            "delivery_escalated": self._conn.execute(
                "SELECT COUNT(*) as c FROM delivery_attempts WHERE status='escalated'"
            ).fetchone()["c"],
        }

    def get_unread_summary(self, member: str, since_rowid: int | None = None) -> dict[str, Any]:
        """Get unread message summary for a member.

        Returns count, latest message details, and unread by channel.
        If since_rowid is not provided, uses unacked messages (acked_by='[]').

        Args:
            member: Member identity (e.g. 'lingflow')
            since_rowid: Start from this rowid. If None, counts unacked only.

        Returns:
            {"count": int, "latest": {...}|None, "by_channel": {...}}
        """
        if since_rowid is not None:
            count_row = self._conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all')",
                (since_rowid, member),
            ).fetchone()
            latest_row = self._conn.execute(
                "SELECT rowid, message_id, thread_id, sender, subject, body, timestamp, channel "
                "FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') "
                "ORDER BY rowid DESC LIMIT 1",
                (since_rowid, member),
            ).fetchone()
            channel_rows = self._conn.execute(
                "SELECT channel, COUNT(*) as c FROM messages "
                "WHERE rowid > ? AND (recipient = ? OR recipient = 'all') "
                "GROUP BY channel ORDER BY c DESC",
                (since_rowid, member),
            ).fetchall()
        else:
            count_row = self._conn.execute(
                "SELECT COUNT(*) as c FROM messages "
                "WHERE (recipient = ? OR recipient = 'all') AND acked_by = '[]'",
                (member,),
            ).fetchone()
            latest_row = self._conn.execute(
                "SELECT rowid, message_id, thread_id, sender, subject, body, timestamp, channel "
                "FROM messages "
                "WHERE (recipient = ? OR recipient = 'all') AND acked_by = '[]' "
                "ORDER BY rowid DESC LIMIT 1",
                (member,),
            ).fetchone()
            channel_rows = self._conn.execute(
                "SELECT channel, COUNT(*) as c FROM messages "
                "WHERE (recipient = ? OR recipient = 'all') AND acked_by = '[]' "
                "GROUP BY channel ORDER BY c DESC",
                (member,),
            ).fetchall()

        latest = None
        if latest_row:
            latest = {
                "rowid": latest_row["rowid"],
                "message_id": latest_row["message_id"],
                "thread_id": latest_row["thread_id"],
                "sender": latest_row["sender"],
                "subject": latest_row["subject"],
                "body": latest_row["body"][:200],
                "timestamp": latest_row["timestamp"],
                "channel": latest_row["channel"],
            }

        by_channel = {r["channel"]: r["c"] for r in channel_rows}

        return {
            "member": member,
            "count": count_row["c"],
            "latest": latest,
            "by_channel": by_channel,
        }

    @_serialized_write
    def queue_pending(self, message_id: str, recipients: list[str]) -> int:
        """Queue a message as pending for specific recipients.

        Used when recipients are offline (按需层 members). When they come
        online, they call batch_ack to retrieve and acknowledge all pending.

        Args:
            message_id: The message to queue
            recipients: List of member identities to queue for

        Returns:
            Number of pending entries created
        """
        now = _now_iso()
        count = 0
        for r in recipients:
            if r == "all":
                continue
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO pending_for (message_id, recipient, queued_at) VALUES (?, ?, ?)",
                    (message_id, r, now),
                )
                count += 1
            except Exception:
                logger.warning("queue_pending: failed for message=%s recipient=%s", message_id, r)
        self._conn.commit()
        return count

    def get_pending(self, recipient: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Get all unacknowledged pending messages for a recipient.

        Args:
            recipient: Member identity
            limit: Max messages to return

        Returns:
            List of pending message dicts with message details
        """
        rows = self._conn.execute(
            "SELECT pf.message_id, pf.queued_at, m.thread_id, m.sender, "
            "m.subject, m.body, m.timestamp, m.channel "
            "FROM pending_for pf JOIN messages m ON pf.message_id = m.message_id "
            "WHERE pf.recipient = ? AND pf.acked = 0 "
            "ORDER BY pf.queued_at ASC LIMIT ?",
            (recipient, limit),
        ).fetchall()
        return [
            {
                "message_id": r["message_id"],
                "thread_id": r["thread_id"],
                "sender": r["sender"],
                "subject": r["subject"],
                "body": r["body"],
                "timestamp": r["timestamp"],
                "channel": r["channel"],
                "queued_at": r["queued_at"],
            }
            for r in rows
        ]

    @_serialized_write
    def batch_ack(self, recipient: str) -> int:
        """Acknowledge all pending messages for a recipient.

        Called when a member comes online to clear their pending queue.
        Also updates the acked_by field on the actual messages.

        Args:
            recipient: Member identity

        Returns:
            Number of messages acknowledged
        """
        now = _now_iso()
        pending_rows = self._conn.execute(
            "SELECT message_id FROM pending_for WHERE recipient = ? AND acked = 0",
            (recipient,),
        ).fetchall()
        if not pending_rows:
            return 0

        message_ids = [r["message_id"] for r in pending_rows]
        self._conn.execute(
            "UPDATE pending_for SET acked = 1, acked_at = ? WHERE recipient = ? AND acked = 0",
            (now, recipient),
        )
        for mid in message_ids:
            row = self._conn.execute(
                "SELECT acked_by FROM messages WHERE message_id = ?", (mid,)
            ).fetchone()
            if row:
                acked = json.loads(row["acked_by"])
                if recipient not in acked:
                    acked.append(recipient)
                    self._conn.execute(
                        "UPDATE messages SET acked_by = ? WHERE message_id = ?",
                        (json.dumps(acked), mid),
                    )
        self._conn.commit()
        return len(message_ids)

    def pending_count(self, recipient: str) -> int:
        """Count unacknowledged pending messages for a recipient."""
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM pending_for WHERE recipient = ? AND acked = 0",
            (recipient,),
        ).fetchone()
        return row["c"]

    # ------------------------------------------------------------------
    # Identity file write authorization (verify_write_auth)
    # ------------------------------------------------------------------

    _PROTECTED_FILENAMES = {"CRUSH.md", "AGENTS.md"}
    _AUTH_TTL_MINUTES = 30

    def verify_write_auth(self, file_path: str, caller: str, intent: str = "") -> dict[str, Any]:
        """Check whether a write to a protected identity file is authorized.

        Authorization sources (any one suffices):
        1. User message to caller within last 15 min
        2. Approved governance proposal covering the modification
        3. Caller posted intent + >=1 other member replied agreement within 24h
        4. Valid unexpired prior authorization in write_auth_log

        Returns dict with keys: authorized, source, reason, auth_id
        """
        import os as _os
        basename = _os.path.basename(file_path)
        is_protected = basename in self._PROTECTED_FILENAMES

        if not is_protected:
            return {"authorized": True, "source": "not_protected", "reason": f"{basename} is not a protected file", "auth_id": ""}

        if caller == "lingmessage":
            return {"authorized": False, "source": "self_review_excluded", "reason": "lingmessage cannot authorize its own writes; need external member confirmation", "auth_id": ""}

        now = _now_iso()
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(minutes=self._AUTH_TTL_MINUTES)).isoformat()

        self._conn.execute(
            "DELETE FROM write_auth_log WHERE expires_at < ?", (now,)
        )
        self._conn.commit()

        auth_15min = (now_dt - timedelta(minutes=15)).isoformat()
        user_msgs = self._conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE sender IN ('webui_user','user') AND recipient = ? AND timestamp > ?",
            (caller, auth_15min),
        ).fetchone()["c"]
        if user_msgs > 0:
            auth_id = _new_id()
            self._conn.execute(
                "INSERT INTO write_auth_log (id, caller, file_path, intent, auth_source, auth_thread_id, timestamp, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (auth_id, caller, file_path, intent, "user_message", "", now, expires),
            )
            self._conn.commit()
            return {"authorized": True, "source": "user_message", "reason": f"user message to {caller} within 15min", "auth_id": auth_id}

        intent_24h = (now_dt - timedelta(hours=24)).isoformat()
        intent_rows = self._conn.execute(
            "SELECT thread_id, body FROM messages WHERE sender = ? AND timestamp > ? AND "
            "(body LIKE ? OR body LIKE ? OR subject LIKE ?) ORDER BY rowid DESC LIMIT 5",
            (caller, intent_24h, "%CRUSH.md%", "%AGENTS.md%", "%身份文件%"),
        ).fetchall()

        for irow in intent_rows:
            replies = self._conn.execute(
                "SELECT sender FROM messages WHERE thread_id = ? AND sender != ? AND "
                "(body LIKE '%同意%' OR body LIKE '%approve%' OR body LIKE '%同意%' OR body LIKE '%支持%' OR body LIKE '%确认%')",
                (irow["thread_id"], caller),
            ).fetchall()
            if replies:
                agreer = replies[0]["sender"]
                auth_id = _new_id()
                self._conn.execute(
                    "INSERT INTO write_auth_log (id, caller, file_path, intent, auth_source, auth_thread_id, timestamp, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                    (auth_id, caller, file_path, intent, "member_confirmation", irow["thread_id"], now, expires),
                )
                self._conn.commit()
                return {"authorized": True, "source": "member_confirmation", "reason": f"{agreer} confirmed in thread {irow['thread_id'][:12]}...", "auth_id": auth_id}

        existing = self._conn.execute(
            "SELECT id, auth_source, auth_thread_id FROM write_auth_log WHERE caller = ? AND file_path = ? AND expires_at > ? ORDER BY timestamp DESC LIMIT 1",
            (caller, file_path, now),
        ).fetchone()
        if existing:
            return {"authorized": True, "source": existing["auth_source"], "reason": f"prior auth {existing['id'][:12]}... still valid", "auth_id": existing["id"]}

        return {"authorized": False, "source": "none", "reason": "POST_TO_LINGBUS_FIRST: no authorization found. Post intent to LingBus and get >=1 member confirmation.", "auth_id": ""}

    @_serialized_write
    def prune_pending(self, *, older_than_days: int = 30) -> int:
        """Remove acknowledged pending entries older than N days.

        Returns number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM pending_for WHERE acked = 1 AND acked_at < ? AND acked_at != ''",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    @_serialized_write
    def prune_auto_messages(self, *, older_than_days: int = 30) -> dict:
        """Remove auto-generated system messages older than N days.

        Cleans wakeup notifications, session restore checkpoints, interrupt
        monitoring reports, and SDTH tier alerts. Preserves CRITICAL/DOWN
        alerts and all non-system messages.

        Returns dict with keys: deleted, kept_critical, cutoff.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        kept_critical = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp < ? AND channel = 'system' "
            "AND (subject LIKE '%CRITICAL%' OR subject LIKE '%DOWN%')",
            (cutoff,),
        ).fetchone()[0]
        cursor = self._conn.execute(
            "DELETE FROM messages WHERE timestamp < ? AND channel = 'system' "
            "AND (subject LIKE ? OR subject LIKE ? OR subject LIKE ? "
            "OR subject LIKE ? OR subject LIKE ? OR subject LIKE ? "
            "OR subject LIKE ?)",
            (cutoff, "%唤醒%", "%会话恢复%", "%中断监控%",
             "%L1 %", "%L2 %", "%会话停滞%", "%Load%"),
        )
        self._conn.commit()
        return {
            "deleted": cursor.rowcount,
            "kept_critical": kept_critical,
            "cutoff": cutoff,
        }

    @_serialized_write
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
            self._safe_commit()
            imported += 1
        return imported

    @_serialized_write
    def sync_to_mailbox(self, mailbox: Mailbox) -> int:
        """Export all LingBus threads into a Mailbox instance.

        Creates thread directories and message files for any thread that
        does not already exist in the Mailbox. Individual messages are
        deduplicated by ``message_id`` (idempotent).

        Returns the number of threads exported.
        """
        from lingmessage.types import (
            Channel as MbChannel,
            LingIdentity as MbLingIdentity,
            Message as MbMessage,
            MessageType as MbMessageType,
            SourceType as MbSourceType,
            ThreadHeader as MbThreadHeader,
            ThreadStatus as MbThreadStatus,
        )

        def _safe_enum(enum_cls: type, value: str, default):
            try:
                return enum_cls(value)
            except ValueError:
                return default

        exported = 0
        thread_rows = self._conn.execute(
            "SELECT * FROM threads ORDER BY created_at"
        ).fetchall()

        for t_row in thread_rows:
            tid = t_row["thread_id"]
            existing = mailbox.load_thread_header(tid)
            if existing is not None:
                continue

            participants = json.loads(t_row["participants"])
            channel = _safe_enum(MbChannel, t_row["channel"], MbChannel.ECOSYSTEM)
            status = _safe_enum(MbThreadStatus, t_row["status"], MbThreadStatus.ACTIVE)

            msg_rows = self._conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY rowid",
                (tid,),
            ).fetchall()

            if not msg_rows:
                continue

            header = MbThreadHeader(
                thread_id=tid,
                topic=t_row["topic"],
                channel=channel,
                status=status,
                participants=tuple(participants),
                created_at=t_row["created_at"],
                updated_at=t_row["updated_at"],
                message_count=len(msg_rows),
            )

            thread_dir = mailbox._threads_dir() / tid
            thread_dir.mkdir(parents=True, exist_ok=True)
            header_path = thread_dir / "thread.json"
            import tempfile as _tf

            tmp_fd, tmp_path = _tf.mkstemp(dir=thread_dir, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(header.to_json(indent=2))
                os.replace(tmp_path, header_path)
                os.chmod(header_path, 0o600)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            for m_row in msg_rows:
                mid = m_row["message_id"]
                msg_path = thread_dir / f"msg_{mid}.json"
                if msg_path.exists():
                    continue

                sender = _safe_enum(MbLingIdentity, m_row["sender"], MbLingIdentity.ALL)
                recipient = _safe_enum(MbLingIdentity, m_row["recipient"], MbLingIdentity.ALL)
                msg_type = _safe_enum(MbMessageType, m_row["message_type"], MbMessageType.REPLY)
                src_type = _safe_enum(MbSourceType, m_row["source_type"] if "source_type" in m_row.keys() else "inferred", MbSourceType.INFERRED)

                raw_meta = m_row["metadata"] or "{}"
                raw_meta_dict = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                safe_meta = {k: v for k, v in (raw_meta_dict or {}).items() if isinstance(k, str) and isinstance(v, str) and len(k) <= 100 and len(v) <= 1000}

                msg = MbMessage(
                    message_id=mid,
                    thread_id=tid,
                    sender=sender,
                    recipient=recipient,
                    message_type=msg_type,
                    channel=channel,
                    subject=m_row["subject"],
                    body=m_row["body"],
                    timestamp=m_row["timestamp"],
                    reply_to=m_row["reply_to"] or "",
                    metadata=tuple(sorted(safe_meta.items())),
                    source_type=src_type,
                    source_trace=m_row["source_trace"] if "source_trace" in m_row.keys() else "",
                )

                m_tmp_fd, m_tmp_path = _tf.mkstemp(dir=thread_dir, suffix=".tmp")
                try:
                    with os.fdopen(m_tmp_fd, "w", encoding="utf-8") as f:
                        f.write(msg.to_json(indent=2))
                    os.replace(m_tmp_path, msg_path)
                    os.chmod(msg_path, 0o600)
                except BaseException:
                    try:
                        os.unlink(m_tmp_path)
                    except OSError:
                        pass
                    raise

            mailbox._update_index(
                MbMessage(
                    message_id=msg_rows[-1]["message_id"],
                    thread_id=tid,
                    sender=MbLingIdentity.ALL,
                    recipient=MbLingIdentity.ALL,
                    message_type=MbMessageType.OPEN,
                    channel=channel,
                    subject="",
                    body="",
                    timestamp=t_row["created_at"],
                ),
                header,
            )
            exported += 1

        return exported
