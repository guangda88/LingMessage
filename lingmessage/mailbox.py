"""灵信共享邮箱 — 所有灵项目读写的公共讨论区"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingmessage.types import (
    Channel,
    DeliveryStatus,
    LingIdentity,
    Message,
    MessageType,
    SourceType,
    ThreadHeader,
    ThreadStatus,
    _new_id,
    _now_iso,
    create_message,
    create_thread_header,
    mark_delivered,
)

logger = logging.getLogger(__name__)


@dataclass
class AuditLogEntry:
    """Audit log entry for tracking important operations."""

    timestamp: str
    operation: str
    thread_id: str
    message_id: str
    sender: str
    details: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "sender": self.sender,
            "details": self.details,
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False)


class _FileLock:
    """跨平台文件锁上下文管理器"""

    def __init__(self, path: Path, timeout: float = 10.0):
        self._path = path
        self._timeout = timeout
        self._lock_file = path.parent / f"{path.name}.lock"
        self._locked = False

    def __enter__(self) -> "_FileLock":
        import fcntl
        import time

        # VULN-05: Stale lock detection — remove locks older than 60s
        if self._lock_file.exists():
            try:
                lock_age = time.time() - self._lock_file.stat().st_mtime
                if lock_age > 60:
                    logger.warning(f"Removing stale lock file (age={lock_age:.0f}s): {self._lock_file}")
                    self._lock_file.unlink(missing_ok=True)
            except OSError:
                pass

        start_time = time.time()
        while True:
            try:
                self._lock_file.touch(exist_ok=True)
                self._fd = os.open(self._lock_file, os.O_RDWR)
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                return self
            except (BlockingIOError, OSError):
                if time.time() - start_time > self._timeout:
                    raise TimeoutError(f"Could not acquire lock on {self._path} after {self._timeout}s")
                time.sleep(0.01)

    def __exit__(self, *args: object) -> None:
        import fcntl

        if self._locked:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            try:
                self._lock_file.unlink()
            except FileNotFoundError:
                pass
            self._locked = False


@dataclass
class Mailbox:
    root: Path = field(default_factory=lambda: Path.home() / ".lingmessage")

    def _threads_dir(self) -> Path:
        return self.root / "threads"

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _index_backup_path(self) -> Path:
        """Get the backup path for index.json."""
        return Path(str(self._index_path()) + ".backup")

    def _create_index_backup(self) -> None:
        """Create a backup of the current index.json file."""
        index_path = self._index_path()
        backup_path = self._index_backup_path()

        if index_path.exists():
            try:
                import shutil
                shutil.copy2(index_path, backup_path)
                logger.debug(f"Created index backup at {backup_path}")
            except OSError as e:
                logger.warning(f"Failed to create index backup: {e}")

    def _restore_from_backup(self) -> bool:
        """Restore index.json from backup if backup exists.

        Returns:
            True if restoration succeeded, False otherwise
        """
        backup_path = self._index_backup_path()
        index_path = self._index_path()

        if not backup_path.exists():
            return False

        try:
            import shutil
            shutil.copy2(backup_path, index_path)
            logger.info("Restored index from backup")
            return True
        except OSError as e:
            logger.error(f"Failed to restore index from backup: {e}")
            return False

    # --- Input Validation ---

    _SAFE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
    _MAX_JSON_SIZE = 10 * 1024 * 1024  # 10MB

    @staticmethod
    def _is_safe_id(value: str) -> bool:
        """Check whether an ID matches the expected 32-hex-char format."""
        return bool(Mailbox._SAFE_ID_RE.fullmatch(value))

    @staticmethod
    def _validate_id(value: str, name: str) -> None:
        """Validate that an ID matches the expected hex UUID format."""
        if not Mailbox._SAFE_ID_RE.fullmatch(value):
            raise ValueError(f"Invalid {name}: must be 32 hex characters, got {value!r}")

    def _safe_thread_path(self, thread_id: str) -> Path:
        """Resolve and validate a thread directory path.

        Raises ValueError for malformed or path-traversal IDs.
        """
        self._validate_id(thread_id, "thread_id")
        resolved = (self._threads_dir() / thread_id).resolve()
        if not str(resolved).startswith(str(self._threads_dir().resolve())):
            raise ValueError(f"thread_id path traversal detected: {thread_id}")
        return resolved

    def _safe_msg_path(self, thread_id: str, message_id: str) -> Path:
        """Resolve and validate a message file path."""
        self._validate_id(message_id, "message_id")
        d = self._safe_thread_path(thread_id)
        resolved = (d / f"msg_{message_id}.json").resolve()
        if not str(resolved).startswith(str(self._threads_dir().resolve())):
            raise ValueError(f"message_id path traversal detected: {message_id}")
        return resolved

    def _thread_path_unchecked(self, thread_id: str) -> Path:
        """Return thread dir path *without* validation – for read-only lookups."""
        return self._threads_dir() / thread_id

    @staticmethod
    def _read_json_safe(path: Path) -> Any:
        """Read and parse JSON with size limit."""
        size = path.stat().st_size
        if size > Mailbox._MAX_JSON_SIZE:
            raise ValueError(f"File too large ({size} bytes): {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_permissions(self) -> None:
        """Set restrictive permissions on mailbox root and key files."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
            secret_key = self.root / ".secret_key"
            if secret_key.exists():
                os.chmod(secret_key, 0o600)
            audit_log = self.root / "audit.log"
            if audit_log.exists():
                os.chmod(audit_log, 0o600)
            index = self._index_path()
            if index.exists():
                os.chmod(index, 0o600)
        except OSError:
            pass

    # --- End Input Validation ---

    def _thread_dir(self, thread_id: str) -> Path:
        d = self._safe_thread_path(thread_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_secret_key(self) -> str:
        """Get secret key for signature verification.

        First checks environment variable, then key file.
        Returns empty string if not configured (signature verification disabled).
        """
        # Check environment variable
        key = os.environ.get("LINGMESSAGE_SECRET_KEY", "")
        if key:
            return key

        # Check key file
        key_file = self.root / ".secret_key"
        if key_file.exists():
            try:
                return key_file.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to read secret key file: {e}")

        return ""

    def _audit_path(self) -> Path:
        """Get the audit log file path."""
        return self.root / "audit.log"

    def _log_audit(self, entry: AuditLogEntry) -> None:
        """Append an audit log entry to the audit log file.

        Each entry includes an HMAC hash chain for tamper detection.
        The hash of each line incorporates the hash of the previous line,
        making any deletion or modification detectable.

        Args:
            entry: The audit log entry to append
        """
        audit_path = self._audit_path()
        try:
            prev_hash = ""
            if audit_path.exists():
                lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
                if lines and lines[-1].strip():
                    try:
                        prev_data = json.loads(lines[-1])
                        prev_hash = prev_data.get("_chain_hash", "")
                    except (json.JSONDecodeError, KeyError):
                        pass
            entry_dict = entry.to_dict()
            chain_input = prev_hash + json.dumps(entry_dict, sort_keys=True, separators=(",", ":"))
            chain_hash = hmac.new(
                b"lingmessage-audit-chain-v1",
                chain_input.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            entry_dict["_chain_hash"] = chain_hash
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry_dict, ensure_ascii=False) + "\n")
            logger.debug(f"Audit log: {entry.operation} by {entry.sender} on {entry.message_id}")
        except OSError as e:
            logger.error(f"Failed to write audit log: {e}")

    def get_audit_log(self, limit: int = 100) -> list[AuditLogEntry]:
        """Read recent audit log entries.

        Args:
            limit: Maximum number of entries to return (default: 100)

        Returns:
            List of audit log entries, most recent first
        """
        audit_path = self._audit_path()
        if not audit_path.exists():
            return []

        entries: list[AuditLogEntry] = []
        try:
            lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
            for line in reversed(lines[-limit:]):
                if line.strip():
                    import json
                    data = json.loads(line)
                    entries.append(AuditLogEntry(
                        timestamp=data["timestamp"],
                        operation=data["operation"],
                        thread_id=data["thread_id"],
                        message_id=data["message_id"],
                        sender=data["sender"],
                        details=data.get("details", ""),
                    ))
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to read audit log: {e}")

        return entries

    def post(self, message: Message, signature: str = "") -> Message:
        """Post a message to the mailbox.

        If message.source_type is VERIFIED, signature verification is performed.
        When a VERIFIED message arrives without a pre-computed signature but
        a secret key is configured, the signature is computed on the fly.

        Args:
            message: The message to post
            signature: Optional pre-computed signature for verification

        Returns:
            The posted message

        Raises:
            ValueError: If signature verification fails
        """
        if message.source_type == SourceType.VERIFIED:
            secret_key = self._get_secret_key()
            if not secret_key:
                raise ValueError(
                    f"Message {message.message_id} marked as VERIFIED "
                    f"but no secret key configured (set LINGMESSAGE_SECRET_KEY "
                    f"or create ~/.lingmessage/.secret_key)"
                )
            from lingmessage.signing import sign_message
            computed = sign_message(message, secret_key)
            if signature:
                if not hmac.compare_digest(computed, signature):
                    raise ValueError(f"Invalid signature for message {message.message_id}")
            signature = computed
            logger.info(f"Message {message.message_id} signature verified successfully")

        # Log message posting
        logger.info(f"Posting message {message.message_id} in thread {message.thread_id} from {message.sender.value}")

        # Write message file
        d = self._thread_dir(message.thread_id)
        msg_path = d / f"msg_{message.message_id}.json"
        tmp_fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(message.to_json(indent=2))
            os.replace(tmp_path, msg_path)
            os.chmod(msg_path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Update index
        self._update_index(message)

        # Log audit entry
        audit_entry = AuditLogEntry(
            timestamp=_now_iso(),
            operation="post_message",
            thread_id=message.thread_id,
            message_id=message.message_id,
            sender=message.sender.value,
            details=f"source_type={message.source_type.value}, signed={bool(signature) if message.source_type == SourceType.VERIFIED else False}",
        )
        self._log_audit(audit_entry)

        # Ding recipient
        if message.recipient and message.recipient.value != message.sender.value:
            try:
                from lingmessage.notify import ding_recipient

                ding_recipient(message.recipient.value, {
                    "type": "new_message",
                    "thread_id": message.thread_id,
                    "message_id": message.message_id,
                    "sender": message.sender.value,
                    "subject": message.subject,
                })
            except Exception:
                pass

        return message

    def open_thread(
        self,
        sender: LingIdentity,
        recipients: tuple[LingIdentity, ...],
        channel: Channel,
        topic: str,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.OPEN,
        metadata: dict[str, str] | None = None,
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> tuple[ThreadHeader, Message]:
        """Open a new thread with the first message.

        Args:
            sender: The sender identity
            recipients: Tuple of recipient identities
            channel: The discussion channel
            topic: Thread topic
            subject: Message subject
            body: Message body
            message_type: Type of message
            metadata: Optional metadata dictionary
            source_type: Source type of the message
            source_trace: Source trace string
            signature: Optional signature for verification

        Returns:
            Tuple of (ThreadHeader, Message)
        """
        tid = _new_id()
        first = create_message(
            sender=sender,
            recipient=LingIdentity.ALL,
            message_type=message_type,
            channel=channel,
            subject=subject,
            body=body,
            thread_id=tid,
            metadata=metadata,
            source_type=source_type,
            source_trace=source_trace,
        )
        header = create_thread_header(
            topic=topic,
            channel=channel,
            participants=recipients + (sender,),
            first_message=first,
        )
        d = self._thread_dir(tid)
        header_path = d / "thread.json"
        tmp_fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
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
        self.post(first, signature)
        self._update_index(first, header)

        # Log audit entry for thread creation
        audit_entry = AuditLogEntry(
            timestamp=_now_iso(),
            operation="open_thread",
            thread_id=tid,
            message_id=first.message_id,
            sender=sender.value,
            details=f"topic={topic}, channel={channel.value}",
        )
        self._log_audit(audit_entry)

        return header, first

    def reply(
        self,
        thread_id: str,
        sender: LingIdentity,
        recipient: LingIdentity,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.REPLY,
        metadata: dict[str, str] | None = None,
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> Message:
        """Reply to an existing thread.

        Args:
            thread_id: The thread ID to reply to
            sender: The sender identity
            recipient: The recipient identity
            subject: Reply subject
            body: Reply body
            message_type: Type of message
            metadata: Optional metadata dictionary
            source_type: Source type of the message
            source_trace: Source trace string
            signature: Optional signature for verification

        Returns:
            The created reply message

        Raises:
            ValueError: If thread does not exist
        """
        header = self.load_thread_header(thread_id)
        if header is None:
            raise ValueError(f"Thread {thread_id} not found")
        msg = create_message(
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            channel=Channel(header.channel),
            subject=subject,
            body=body,
            thread_id=thread_id,
            reply_to="",
            metadata=metadata,
            source_type=source_type,
            source_trace=source_trace,
        )
        self.post(msg, signature)
        self._increment_thread(header)
        return msg

    def load_thread_header(self, thread_id: str) -> ThreadHeader | None:
        path = self._thread_path_unchecked(thread_id) / "thread.json"
        if not path.exists():
            return None
        data = self._read_json_safe(path)
        return ThreadHeader.from_dict(data)

    def load_thread_messages(self, thread_id: str) -> tuple[Message, ...]:
        d = self._thread_path_unchecked(thread_id)
        if not d.exists():
            return ()
        messages: list[Message] = []
        for p in d.glob("msg_*.json"):
            data = self._read_json_safe(p)
            messages.append(Message.from_dict(data))
        messages.sort(key=lambda m: m.timestamp)
        return tuple(messages)

    def load_thread_messages_iter(self, thread_id: str):
        """Load thread messages lazily using a generator.

        Reads each file once, holding at most one parsed Message object
        plus a small index of (timestamp, raw_dict) pairs in memory.
        Messages are yielded in chronological order.

        Args:
            thread_id: The thread ID to load messages from

        Yields:
            Message objects in chronological order
        """
        d = self._thread_path_unchecked(thread_id)
        if not d.exists():
            return
        paths = list(d.glob("msg_*.json"))
        if not paths:
            return
        index: list[tuple[str, dict[str, Any]]] = []
        for p in paths:
            try:
                data = self._read_json_safe(p)
                ts = data.get("timestamp", "")
                index.append((ts, data))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
        index.sort(key=lambda pair: pair[0])
        for _, data in index:
            try:
                yield Message.from_dict(data)
            except (ValueError, KeyError):
                continue

    def list_threads(
        self,
        channel: Channel | None = None,
        status: ThreadStatus | None = None,
        participant: LingIdentity | None = None,
    ) -> tuple[ThreadHeader, ...]:
        index = self._load_index()
        results: list[ThreadHeader] = []
        for entry in index.get("threads", []):
            h = ThreadHeader.from_dict(entry)
            if channel is not None and h.channel != channel.value:
                continue
            if status is not None and h.status != status.value:
                continue
            if participant is not None and participant.value not in h.participants:
                continue
            results.append(h)
        return tuple(results)

    def _load_index(self) -> dict[str, Any]:
        """Load index from file, with automatic backup recovery."""
        path = self._index_path()
        if not path.exists():
            return {"threads": [], "last_updated": _now_iso()}

        try:
            data = self._read_json_safe(path)
            if isinstance(data, dict):
                return data
            logger.warning("Index file is not a valid dict, will try backup")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.error(f"Failed to load index: {e}, will try backup")

        # Try to restore from backup
        if self._restore_from_backup():
            try:
                data = self._read_json_safe(path)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError, ValueError) as e:
                logger.error(f"Backup also corrupted: {e}")

        # If all else fails, return empty index
        logger.warning("Starting with empty index after recovery failed")
        return {"threads": [], "last_updated": _now_iso()}

    def _update_index(
        self,
        message: Message,
        header: ThreadHeader | None = None,
    ) -> None:
        index_path = self._index_path()
        with _FileLock(index_path):
            index = self._load_index()
            tid = message.thread_id
            threads = index.get("threads", [])
            found = False
            for i, t in enumerate(threads):
                t_id = t.get("thread_id") or t.get("id", "")
                if t_id == tid:
                    threads[i]["updated_at"] = _now_iso()
                    threads[i]["message_count"] = threads[i].get("message_count", 0) + 1
                    if header is not None:
                        threads[i]["topic"] = header.topic
                        threads[i]["channel"] = header.channel
                        threads[i]["status"] = header.status
                        threads[i]["participants"] = list(header.participants)
                    found = True
                    break
            if not found and header is not None:
                threads.append(header.to_dict())
            index["threads"] = threads
            index["last_updated"] = _now_iso()
            self.root.mkdir(parents=True, exist_ok=True)
            self._ensure_permissions()
            self._create_index_backup()
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self.root), suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(index, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, index_path)
                os.chmod(index_path, 0o600)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def _increment_thread(self, header: ThreadHeader) -> None:
        index_path = self._index_path()
        with _FileLock(index_path):
            index = self._load_index()
            tid = header.thread_id
            for i, t in enumerate(index.get("threads", [])):
                t_id = t.get("thread_id") or t.get("id", "")
                if t_id == tid:
                    index["threads"][i]["message_count"] = t.get("message_count", 1) + 1
                    index["threads"][i]["updated_at"] = _now_iso()
                    break
            index["last_updated"] = _now_iso()
            self.root.mkdir(parents=True, exist_ok=True)
            self._ensure_permissions()
            self._create_index_backup()
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self.root), suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(index, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, index_path)
                os.chmod(index_path, 0o600)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def get_summary(self) -> dict[str, Any]:
        index = self._load_index()
        threads = index.get("threads", [])
        by_channel: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_messages = 0
        for t in threads:
            ch = t.get("channel", "unknown")
            st = t.get("status", "unknown")
            by_channel[ch] = by_channel.get(ch, 0) + 1
            by_status[st] = by_status.get(st, 0) + 1
            total_messages += t.get("message_count", 0)
        return {
            "total_threads": len(threads),
            "total_messages": total_messages,
            "by_channel": by_channel,
            "by_status": by_status,
            "last_updated": index.get("last_updated", ""),
        }

    def ack_message(self, thread_id: str, message_id: str) -> Message | None:
        """Mark a message as delivered.

        Args:
            thread_id: The thread ID containing the message
            message_id: The message ID to acknowledge

        Returns:
            The updated message, or None if not found
        """
        if not self._is_safe_id(thread_id) or not self._is_safe_id(message_id):
            return None
        msg_path = self._safe_msg_path(thread_id, message_id)
        if not msg_path.exists():
            return None

        with _FileLock(msg_path):
            data = self._read_json_safe(msg_path)
            msg = Message.from_dict(data)
            updated = mark_delivered(msg)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(msg_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(updated.to_json(indent=2))
                os.replace(tmp_path, msg_path)
                os.chmod(msg_path, 0o600)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        self._log_audit(AuditLogEntry(
            timestamp=_now_iso(),
            operation="ack_message",
            thread_id=thread_id,
            message_id=message_id,
            sender=msg.sender.value,
            details="delivery_status=delivered",
        ))
        logger.info(f"Message {message_id} marked as delivered")
        return updated

    def get_delivery_stats(self) -> dict[str, Any]:
        """Get delivery statistics across all threads.

        Returns:
            Dictionary with delivery counts and rates
        """
        threads_dir = self._threads_dir()
        if not threads_dir.exists():
            return {
                "total_messages": 0,
                "delivered": 0,
                "failed": 0,
                "pending": 0,
                "delivery_rate": 0.0,
            }
        total = 0
        delivered = 0
        failed = 0
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            for msg_path in thread_dir.glob("msg_*.json"):
                try:
                    data = json.loads(msg_path.read_text(encoding="utf-8"))
                    total += 1
                    status = data.get("delivery_status", DeliveryStatus.SENT.value)
                    if status == DeliveryStatus.DELIVERED.value:
                        delivered += 1
                    elif status == DeliveryStatus.FAILED.value:
                        failed += 1
                except (json.JSONDecodeError, OSError):
                    total += 1
        return {
            "total_messages": total,
            "delivered": delivered,
            "failed": failed,
            "pending": total - delivered - failed,
            "delivery_rate": delivered / total if total > 0 else 0.0,
        }
