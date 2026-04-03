from __future__ import annotations

"""灵信兼容层 — 桥接灵依现有 lingmessage.py 格式到灵信协议

灵依 (LingYi) 有自己的 lingmessage.py 实现，格式不同于灵信协议。
本模块提供双向转换：
  - LingYi 讨论格式 → 灵信 Thread + Messages
  - 灵信 Messages → 灵依讨论格式

存储路径冲突解决：
  - 灵依使用 ~/.lingmessage/discussions/（单文件嵌入所有消息）
  - 灵信使用 ~/.lingmessage/threads/（每消息一个文件）
  - 两者共享 ~/.lingmessage/index.json，但索引格式不同
  - 建议：灵信使用独立路径，如 ~/.lingmessage/v2/
"""

import json
from pathlib import Path
from typing import Any

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    ThreadHeader,
    ThreadStatus,
    _now_iso,
    _new_id,
    create_message,
    create_thread_header,
)

_IDENTITY_MAP: dict[str, LingIdentity] = {
    "lingflow": LingIdentity.LINGFLOW,
    "lingclaude": LingIdentity.LINGCLAUDE,
    "lingyi": LingIdentity.LINGYI,
    "lingzhi": LingIdentity.LINGZHI,
    "lingtongask": LingIdentity.LINGTONGASK,
    "lingterm": LingIdentity.LINGXI,
    "lingxi": LingIdentity.LINGXI,
    "lingminopt": LingIdentity.LINGMINOPT,
    "lingresearch": LingIdentity.LINGRESEARCH,
    "zhibridge": LingIdentity.LINGZHI,
}

_IDENTITY_REVERSE: dict[LingIdentity, str] = {
    LingIdentity.LINGFLOW: "lingflow",
    LingIdentity.LINGCLAUDE: "lingclaude",
    LingIdentity.LINGYI: "lingyi",
    LingIdentity.LINGZHI: "lingzhi",
    LingIdentity.LINGTONGASK: "lingtongask",
    LingIdentity.LINGXI: "lingterm",
    LingIdentity.LINGMINOPT: "lingminopt",
    LingIdentity.LINGRESEARCH: "lingresearch",
}

_TAG_CHANNEL_MAP: dict[str, Channel] = {
    "战略": Channel.ECOSYSTEM,
    "生态": Channel.ECOSYSTEM,
    "集成": Channel.INTEGRATION,
    "基础设施": Channel.SHARED_INFRA,
    "知识": Channel.KNOWLEDGE,
    "优化": Channel.SELF_OPTIMIZE,
    "身份": Channel.IDENTITY,
}


def _resolve_identity(from_id: str) -> LingIdentity:
    return _IDENTITY_MAP.get(from_id, LingIdentity.LINGYI)


def _guess_channel(tags: list[str] | None, topic: str) -> Channel:
    if tags:
        for tag in tags:
            ch = _TAG_CHANNEL_MAP.get(tag)
            if ch is not None:
                return ch
    return Channel.ECOSYSTEM


def import_lingyi_discussion(
    mailbox: Mailbox,
    discussion: dict[str, Any],
) -> tuple[ThreadHeader, ...] | None:
    lingyi_messages = discussion.get("messages", [])
    if not lingyi_messages:
        return None

    first = lingyi_messages[0]
    sender = _resolve_identity(first.get("from_id", "lingyi"))
    topic = discussion.get("topic", first.get("topic", "Untitled"))
    tags = discussion.get("tags") or first.get("tags")
    channel = _guess_channel(tags, topic)

    recipients_set: set[LingIdentity] = set()
    for lm in lingyi_messages:
        fid = _resolve_identity(lm.get("from_id", "lingyi"))
        if fid != sender:
            recipients_set.add(fid)
    recipients = tuple(recipients_set) if recipients_set else (LingIdentity.ALL,)

    header, first_msg = mailbox.open_thread(
        sender=sender,
        recipients=recipients,
        channel=channel,
        topic=topic,
        subject=first.get("topic", topic),
        body=first.get("content", ""),
        metadata={"imported_from": "lingyi", "original_id": first.get("id", "")},
    )

    for lm in lingyi_messages[1:]:
        lsender = _resolve_identity(lm.get("from_id", "lingyi"))
        mailbox.reply(
            thread_id=header.thread_id,
            sender=lsender,
            recipient=LingIdentity.ALL,
            subject=lm.get("topic", f"Re: {topic}"),
            body=lm.get("content", ""),
            metadata={"imported_from": "lingyi", "original_id": lm.get("id", "")},
        )

    return (header,)


def import_lingyi_store(
    mailbox: Mailbox,
    lingyi_root: Path | None = None,
) -> list[str]:
    root = lingyi_root or Path.home() / ".lingmessage"
    discussions_dir = root / "discussions"
    if not discussions_dir.exists():
        return []

    imported: list[str] = []
    for dp in sorted(discussions_dir.glob("disc_*.json")):
        try:
            data = json.loads(dp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        result = import_lingyi_discussion(mailbox, data)
        if result is not None:
            imported.append(result[0].thread_id)
    return imported


def export_to_lingyi_format(messages: tuple[Message, ...]) -> dict[str, Any]:
    if not messages:
        return {"topic": "", "messages": [], "status": "empty"}

    first = messages[0]
    lingyi_msgs: list[dict[str, Any]] = []
    for m in messages:
        from_id = _IDENTITY_REVERSE.get(m.sender, "lingyi")
        lingyi_msgs.append({
            "id": m.message_id,
            "from_id": from_id,
            "topic": m.subject,
            "content": m.body,
            "timestamp": m.timestamp,
            "reply_to": m.reply_to or None,
        })

    return {
        "topic": first.subject,
        "status": "active",
        "tags": [first.channel.value],
        "messages": lingyi_msgs,
        "created_at": lingyi_msgs[0]["timestamp"],
    }
