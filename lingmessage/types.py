from __future__ import annotations

"""灵信核心类型 — 跨灵项目讨论协议的原子单元"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class LingIdentity(str, Enum):
    LINGFLOW = "lingflow"
    LINGCLAUDE = "lingclaude"
    LINGYI = "lingyi"
    LINGZHI = "lingzhi"
    LINGTONGASK = "lingtongask"
    LINGXI = "lingxi"
    LINGMINOPT = "lingminopt"
    LINGRESEARCH = "lingresearch"
    ALL = "all"


class MessageType(str, Enum):
    OPEN = "open"
    REPLY = "reply"
    SUMMARY = "summary"
    DECISION = "decision"
    QUESTION = "question"
    PROPOSAL = "proposal"
    VOTE = "vote"
    CLOSING = "closing"


class ThreadStatus(str, Enum):
    OPEN = "open"
    ACTIVE = "active"
    FROZEN = "frozen"
    DECIDED = "decided"
    CLOSED = "closed"


class Channel(str, Enum):
    ECOSYSTEM = "ecosystem"
    INTEGRATION = "integration"
    SHARED_INFRA = "shared-infra"
    KNOWLEDGE = "knowledge"
    SELF_OPTIMIZE = "self-optimize"
    IDENTITY = "identity"


@dataclass(frozen=True)
class Message:
    message_id: str
    thread_id: str
    sender: LingIdentity
    recipient: LingIdentity
    message_type: MessageType
    channel: Channel
    subject: str
    body: str
    timestamp: str
    reply_to: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "sender": self.sender.value,
            "recipient": self.recipient.value,
            "message_type": self.message_type.value,
            "channel": self.channel.value,
            "subject": self.subject,
            "body": self.body,
            "timestamp": self.timestamp,
        }
        if self.reply_to:
            d["reply_to"] = self.reply_to
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        return d

    def to_json(self, indent: int = 0) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent or None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            message_id=data["message_id"],
            thread_id=data["thread_id"],
            sender=LingIdentity(data["sender"]),
            recipient=LingIdentity(data["recipient"]),
            message_type=MessageType(data["message_type"]),
            channel=Channel(data["channel"]),
            subject=data["subject"],
            body=data["body"],
            timestamp=data["timestamp"],
            reply_to=data.get("reply_to", ""),
            metadata=tuple(sorted(data.get("metadata", {}).items())),
        )


@dataclass(frozen=True)
class ThreadHeader:
    thread_id: str
    topic: str
    channel: Channel
    status: ThreadStatus
    participants: tuple[str, ...]
    created_at: str
    updated_at: str
    message_count: int = 0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "thread_id": self.thread_id,
            "topic": self.topic,
            "channel": self.channel.value,
            "status": self.status.value,
            "participants": list(self.participants),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }
        if self.summary:
            d["summary"] = self.summary
        return d

    def to_json(self, indent: int = 0) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent or None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreadHeader:
        return cls(
            thread_id=data["thread_id"],
            topic=data["topic"],
            channel=Channel(data["channel"]),
            status=ThreadStatus(data["status"]),
            participants=tuple(data.get("participants", [])),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            message_count=data.get("message_count", 0),
            summary=data.get("summary", ""),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex[:16]


def create_message(
    sender: LingIdentity,
    recipient: LingIdentity,
    message_type: MessageType,
    channel: Channel,
    subject: str,
    body: str,
    thread_id: str = "",
    reply_to: str = "",
    metadata: dict[str, str] | None = None,
) -> Message:
    tid = thread_id or _new_id()
    return Message(
        message_id=_new_id(),
        thread_id=tid,
        sender=sender,
        recipient=recipient,
        message_type=message_type,
        channel=channel,
        subject=subject,
        body=body,
        timestamp=_now_iso(),
        reply_to=reply_to,
        metadata=tuple(sorted((metadata or {}).items())),
    )


def create_thread_header(
    topic: str,
    channel: Channel,
    participants: tuple[LingIdentity, ...],
    first_message: Message,
) -> ThreadHeader:
    now = _now_iso()
    return ThreadHeader(
        thread_id=first_message.thread_id,
        topic=topic,
        channel=channel,
        status=ThreadStatus.ACTIVE,
        participants=tuple(p.value for p in participants),
        created_at=now,
        updated_at=now,
        message_count=1,
    )
