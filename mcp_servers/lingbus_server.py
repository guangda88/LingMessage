"""灵信统一 MCP Server — 消息总线 + 签名验证 + 数据标注（consolidated）"""

import hashlib
import hmac
import io
import logging
import os
import threading
from pathlib import Path

from fastmcp import FastMCP

from lingmessage.lingbus import LingBus
from lingmessage.governance import VoteValue, cast_vote, propose
from lingmessage.signing import annotate_as_verified, sign_message, verify_signature
from lingmessage.types import (
    Channel as MbChannel,
    LingIdentity as MbLing,
    Message,
    MessageType,
    SourceType,
    create_message,
)
from lingmessage.annotate import (
    _load_raw_messages,
    annotate_all,
    detect_rapid_succession_batches,
    detect_same_second_anomalies,
    print_report,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("lingmessage-lingbus")

BUS_DIR = Path.home() / ".lingmessage"
DEFAULT_DB_PATH = str(BUS_DIR / "lingbus.db")

_bus_pool: dict[str, LingBus] = {}
_bus_lock = threading.Lock()


VALID_IDENTITIES = frozenset({
    "lingflow", "lingclaude", "lingresearch", "lingzhi", "lingtongask",
    "lingxi", "lingmessage", "lingminopt", "lingyang", "zhibridge",
    "lingweb", "lingcreate", "lingflow_plus", "all",
})

VALID_VOTE_VALUES = frozenset({"approve", "reject", "abstain"})

VALID_REDZONE_CATEGORIES = frozenset({
    "kill_process", "delete_data", "modify_constraint", "modify_infra",
    "budget_exceed", "modify_membership", "other",
})

_ALLOWED_DB_PREFIX = Path.home() / ".lingmessage"
_CALLER_SECRET = os.environ.get("LINGMESSAGE_CALLER_SECRET", "")
_NOTIFY_FLAG = BUS_DIR / ".new_msg"
_FORCE_SIGNATURE_ENDPOINTS = True
_SIGNING_KEY = os.environ.get("LINGMESSAGE_SIGNING_KEY", "")


def _resolve_db_dir(db_path: str | None) -> Path:
    resolved = Path(db_path or DEFAULT_DB_PATH).expanduser().resolve()
    if not str(resolved).startswith(str(_ALLOWED_DB_PREFIX.resolve())):
        raise ValueError(f"db_path must be under {_ALLOWED_DB_PREFIX}")
    p = resolved
    if p.is_file() or p.suffix:
        p = p.parent
    return p


def _get_bus(db_path: str | None = None) -> LingBus:
    db_dir = _resolve_db_dir(db_path)
    key = str(db_dir)
    if key in _bus_pool:
        return _bus_pool[key]
    with _bus_lock:
        if key not in _bus_pool:
            _bus_pool[key] = LingBus(db_dir)
        return _bus_pool[key]


def _validate_identity(value: str) -> str:
    if value not in VALID_IDENTITIES:
        raise ValueError(f"unknown identity: {value!r}")
    return value


def _validate_caller(identity: str, signature: str) -> str:
    """验证caller身份 + HMAC签名."""
    if identity not in VALID_IDENTITIES:
        raise ValueError(f"unknown identity: {identity!r}")
    if not _CALLER_SECRET:
        raise ValueError("LINGMESSAGE_CALLER_SECRET not set")
    expected = hmac.new(
        _CALLER_SECRET.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError(f"signature mismatch for {identity!r}")
    return identity


def _get_mailbox():
    from lingmessage.mailbox import Mailbox
    return Mailbox(root=BUS_DIR)


def _notify_change() -> None:
    try:
        _NOTIFY_FLAG.touch(exist_ok=True)
    except OSError:
        pass


def _bus_msg_to_dict(m) -> dict:
    return {
        "rowid": m.rowid,
        "thread_id": m.thread_id,
        "message_id": m.message_id,
        "sender": m.sender,
        "recipient": m.recipient,
        "subject": m.subject,
        "body": m.body,
        "timestamp": m.timestamp,
        "channel": m.channel,
    }


def _dict_to_message(data: dict) -> Message:
    """将字典转换为 Message 对象。"""
    if isinstance(data.get("source_type"), str):
        data["source_type"] = SourceType(data["source_type"])
    if "message_id" in data and "thread_id" in data:
        return Message.from_dict(data)
    return create_message(
        sender=MbLing(data["sender"]),
        recipient=MbLing(data.get("recipient", "lingyi")),
        message_type=MessageType(data.get("message_type", "open")),
        channel=MbChannel(data.get("channel", "ecosystem")),
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        thread_id=data.get("thread_id", ""),
    )


def _validate_threads_dir(threads_dir: str) -> Path:
    resolved = Path(threads_dir).expanduser().resolve()
    if not str(resolved).startswith(str(_ALLOWED_DB_PREFIX.resolve())):
        raise ValueError(f"threads_dir must be under {_ALLOWED_DB_PREFIX}")
    return resolved


# ============================================================
# Core messaging tools (keep separate for frequent use)
# ============================================================

@mcp.tool()
def open_thread(
    topic: str,
    sender: str,
    recipients: str,
    body: str = "",
    channel: str = "ecosystem",
    subject: str = "",
    db_path: str | None = None,
    caller_signature: str = "",
) -> dict:
    """在消息总线中创建新线程。"""
    if caller_signature:
        _validate_caller(sender, caller_signature)
    else:
        _validate_identity(sender)
    bus = _get_bus(db_path)
    try:
        with _bus_lock:
            tid, mid = bus.open_thread(
                topic=topic,
                sender=sender,
                recipients=recipients.split(","),
                channel=channel,
                subject=subject,
                body=body,
            )
        _notify_change()
        return {"thread_id": tid, "message_id": mid}
    except ValueError as e:
        msg = str(e)
        if msg.startswith("throttled:"):
            logger.warning("open_thread throttled: sender=%s topic=%s reason=%s", sender, topic, msg)
            return {"error": msg}
        if msg.startswith("invalid channel:"):
            return {"error": msg}
        raise


@mcp.tool()
def post_reply(
    thread_id: str,
    sender: str,
    recipient: str,
    body: str,
    subject: str = "",
    db_path: str | None = None,
    caller_signature: str = "",
) -> dict:
    """在消息总线中回复线程。"""
    if caller_signature:
        _validate_caller(sender, caller_signature)
    else:
        _validate_identity(sender)
    _validate_identity(recipient)
    bus = _get_bus(db_path)
    try:
        with _bus_lock:
            mid = bus.post_reply(
                thread_id=thread_id,
                sender=sender,
                recipient=recipient,
                subject=subject,
                body=body,
            )
        _notify_change()
        return {"message_id": mid}
    except ValueError as e:
        msg = str(e)
        if msg.startswith("throttled:"):
            logger.warning("post_reply throttled: sender=%s thread=%s reason=%s", sender, thread_id, msg)
            return {"error": msg}
        raise


@mcp.tool()
def poll_messages(
    recipient: str,
    since_rowid: int = 0,
    limit: int = 100,
    db_path: str | None = None,
    channels: str | None = None,
) -> list[dict]:
    """轮询接收者的新消息。"""
    _validate_identity(recipient)
    bus = _get_bus(db_path)
    ch_list = [c.strip() for c in channels.split(",")] if channels else None
    msgs = bus.poll(recipient=recipient, since_rowid=since_rowid, limit=limit, channels=ch_list)
    return [_bus_msg_to_dict(m) for m in msgs]


@mcp.tool()
def poll_urgent_messages(
    recipient: str,
    since_rowid: int = 0,
    db_path: str | None = None,
) -> list[dict]:
    """轮询接收者的紧急消息（仅来自用户/webui_user的消息，优先处理）。"""
    _validate_identity(recipient)
    bus = _get_bus(db_path)
    msgs = bus.poll_urgent(recipient=recipient, since_rowid=since_rowid)
    return [_bus_msg_to_dict(m) for m in msgs]


@mcp.tool()
def verify_write_auth(
    file_path: str,
    caller: str,
    intent: str = "",
    db_path: str | None = None,
) -> dict:
    """验证身份文件(CRUSH.md/AGENTS.md)写操作是否有授权。

    授权来源：用户消息(15min内)、治理决议、成员确认(≥1)、未过期历史授权。
    灵信不能审核自己的写操作。
    """
    _validate_identity(caller)
    bus = _get_bus(db_path)
    with _bus_lock:
        return bus.verify_write_auth(file_path=file_path, caller=caller, intent=intent)


@mcp.tool()
def ack_message(message_id: str, member: str, db_path: str | None = None) -> dict:
    """确认消息已读。"""
    _validate_identity(member)
    bus = _get_bus(db_path)
    with _bus_lock:
        bus.ack(message_id=message_id, member=member)
    return {"success": True}


@mcp.tool()
def get_pending_messages(
    member: str,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """获取成员的未确认待处理消息（按需层成员上线时调用）。"""
    _validate_identity(member)
    bus = _get_bus(db_path)
    return bus.get_pending(member, limit=limit)


@mcp.tool()
def redzone_request_approval(
    requester: str,
    category: str,
    reason: str,
    target: str,
    user_message: str = "",
    recipients: str | None = None,
    quorum: int = 2,
    deadline_hours: int = 24,
    caller_signature: str = "",
) -> dict:
    """发起红区操作审批请求。"""
    if _FORCE_SIGNATURE_ENDPOINTS:
        _validate_caller(requester, caller_signature)
    elif caller_signature:
        _validate_caller(requester, caller_signature)
    else:
        _validate_identity(requester)
    if category not in VALID_REDZONE_CATEGORIES:
        raise ValueError(f"invalid redzone category: {category!r}")
    if quorum < 1:
        raise ValueError("quorum must be >= 1")
    from lingmessage.redzone import RedZoneCategory, require_approval
    bus = _get_bus()
    with _bus_lock:
        cat = RedZoneCategory(category)
        recips = recipients.split(",") if recipients else None
        result = require_approval(
            bus,
            requester=requester,
            category=cat,
            reason=reason,
            target=target,
            user_message=user_message,
            recipients=recips,
            quorum=quorum,
            deadline_hours=deadline_hours,
        )
    _notify_change()
    return result


@mcp.tool()
def log_operation(
    caller: str,
    operation: str,
    target: str,
    caller_signature: str = "",
    category: str = "red_zone",
    intent: str = "",
    result: str = "",
    rollback_plan: str = "",
    db_path: str | None = None,
) -> dict:
    """记录红区操作审计日志到LingBus alert频道。"""
    if _FORCE_SIGNATURE_ENDPOINTS:
        _validate_caller(caller, caller_signature)
    else:
        _validate_identity(caller)
        if caller_signature:
            _validate_caller(caller, caller_signature)
    valid_categories = {"red_zone", "dangerous", "routine"}
    if category not in valid_categories:
        raise ValueError(f"invalid category: {category!r}, must be one of {valid_categories}")
    bus = _get_bus(db_path)
    lines = [
        f"**[{category.upper()}]** `{operation}` 操作审计",
        f"- 操作者: {caller}",
        f"- 目标: `{target}`",
    ]
    if intent:
        lines.append(f"- 意图: {intent}")
    if result:
        lines.append(f"- 结果: {result}")
    if rollback_plan:
        lines.append(f"- 回滚方案: {rollback_plan}")
    body = "\n".join(lines)
    topic = f"operation:{caller}:{operation}:{Path(target).name}" if target else f"operation:{caller}:{operation}"
    with _bus_lock:
        tid, mid = bus.open_thread(
            topic=topic,
            sender=caller,
            recipients=["all"],
            channel="alert",
            subject=f"[OP_AUDIT] {caller}: {operation} {Path(target).name}" if target else f"[OP_AUDIT] {caller}: {operation}",
            body=body,
        )
    _notify_change()
    return {"thread_id": tid, "message_id": mid}


# ============================================================
# Consolidated: admin — batch_ack, watch_changes, max_rowid, stats, push_stats, report_deletion
# ============================================================

@mcp.tool()
def admin(
    command: str,
    member: str = "",
    since_rowid: int = 0,
    limit: int = 100,
    db_path: str | None = None,
    caller: str = "",
    caller_signature: str = "",
    event_type: str = "WATCHDOG_ALERT",
    file_path: str = "",
    process_name: str = "",
    process_pid: int = 0,
    process_ppid: int = 0,
    rule_name: str = "",
    detail: str = "",
) -> dict:
    """管理工具：batch_ack (批量确认), watch (增量变更), max_rowid (全局最大rowid), stats (统计), push_stats (推送统计), report_deletion (删除事件)."""
    bus = _get_bus(db_path)

    if command == "batch_ack":
        _validate_identity(member)
        with _bus_lock:
            count = bus.batch_ack(member)
        return {"acked_count": count}

    elif command == "watch":
        if caller:
            _validate_identity(caller)
        msgs = bus.watch_changes(since_rowid, limit=limit)
        return {"changes": [_bus_msg_to_dict(m) for m in msgs]}

    elif command == "max_rowid":
        if caller:
            _validate_identity(caller)
        return {"max_rowid": bus.get_global_max_rowid()}

    elif command == "stats":
        if caller:
            _validate_identity(caller)
        return bus.stats()

    elif command == "push_stats":
        if caller:
            _validate_identity(caller)
        from lingmessage.push_manager import get_push_manager
        return get_push_manager().get_stats()

    elif command == "report_deletion":
        if _FORCE_SIGNATURE_ENDPOINTS:
            _validate_caller(caller, caller_signature)
        else:
            _validate_identity(caller)
            if caller_signature:
                _validate_caller(caller, caller_signature)
        valid_types = {"WATCHDOG_ALERT", "BLOCKED", "ANOMALY"}
        if event_type not in valid_types:
            raise ValueError(f"invalid event_type: {event_type!r}, must be one of {valid_types}")
        lines = [
            f"**[{event_type}]** 文件删除事件",
            f"- 文件: `{file_path}`",
            f"- 进程: {process_name} (PID={process_pid}, PPID={process_ppid})",
            f"- 规则: {rule_name}",
            f"- 来源: {caller}",
        ]
        if detail:
            lines.append(f"- 详情: {detail}")
        body_lines = "\n".join(lines)
        topic = f"delete_event:{Path(file_path).name}" if file_path else f"delete_event:{event_type}"
        with _bus_lock:
            tid, mid = bus.open_thread(
                topic=topic,
                sender="lingmessage",
                recipients=["all"],
                channel="alert",
                subject=f"[{event_type}] {Path(file_path).name}" if file_path else f"[{event_type}]",
                body=body_lines,
            )
        _notify_change()
        return {"thread_id": tid, "message_id": mid}

    else:
        raise ValueError(f"unknown admin command: {command!r}")


# ============================================================
# Consolidated: governance — propose + vote
# ============================================================

@mcp.tool()
def governance(
    command: str,
    proposer: str = "",
    recipients: str = "",
    topic: str = "",
    body: str = "",
    channel: str = "governance",
    quorum: int | None = None,
    deadline_hours: int | None = None,
    caller_signature: str = "",
    thread_id: str = "",
    voter: str = "",
    vote: str = "",
    reason: str = "",
) -> dict:
    """治理工具：propose (发起提案), vote (投票)."""
    if command == "propose":
        if _FORCE_SIGNATURE_ENDPOINTS:
            _validate_caller(proposer, caller_signature)
        elif caller_signature:
            _validate_caller(proposer, caller_signature)
        else:
            _validate_identity(proposer)
        from lingmessage.types import Channel as MbChannel, LingIdentity as MbLing
        mb = _get_mailbox()
        proposer_id = MbLing(proposer)
        recipient_ids = tuple(MbLing(r.strip()) for r in recipients.split(","))
        ch = MbChannel(channel)
        header, msg_obj = propose(
            mb,
            proposer=proposer_id,
            recipients=recipient_ids,
            channel=ch,
            topic=topic,
            body=body,
            quorum=quorum,
            deadline_hours=deadline_hours,
        )
        _notify_change()
        return {"thread_id": header.thread_id, "message_id": msg_obj.message_id}

    elif command == "vote":
        if _FORCE_SIGNATURE_ENDPOINTS:
            _validate_caller(voter, caller_signature)
        elif caller_signature:
            _validate_caller(voter, caller_signature)
        else:
            _validate_identity(voter)
        if vote not in VALID_VOTE_VALUES:
            raise ValueError(f"invalid vote: {vote!r}")
        from lingmessage.types import LingIdentity as MbLing
        mb = _get_mailbox()
        voter_id = MbLing(voter)
        vote_val = VoteValue(vote)
        msg_obj = cast_vote(
            mb,
            thread_id=thread_id,
            voter=voter_id,
            vote=vote_val,
            reason=reason,
        )
        _notify_change()
        return {"message_id": msg_obj.message_id, "vote": vote_val.value}

    else:
        raise ValueError(f"unknown governance command: {command!r}")


# ============================================================
# Consolidated: sign — sign + verify + annotate_verified
# ============================================================

@mcp.tool()
def sign_tool(
    command: str,
    msg: dict = {},
    signature: str = "",
) -> dict:
    """签名工具：sign (签名), verify (验证), annotate_verified (标记已验证)."""
    if not _SIGNING_KEY:
        raise ValueError("LINGMESSAGE_SIGNING_KEY 环境变量未设置")

    if command == "sign":
        message = _dict_to_message(msg)
        sig = sign_message(message, _SIGNING_KEY)
        return {"signature": sig}

    elif command == "verify":
        message = _dict_to_message(msg)
        valid = verify_signature(message, signature, _SIGNING_KEY)
        return {"valid": valid, "source_type": message.source_type.value}

    elif command == "annotate_verified":
        message = _dict_to_message(msg)
        if not verify_signature(message, signature, _SIGNING_KEY):
            raise ValueError("签名验证失败：无法将消息标记为 verified")
        verified = annotate_as_verified(message, signature)
        return verified.to_dict()

    else:
        raise ValueError(f"unknown sign command: {command!r}")


# ============================================================
# Consolidated: annotate — detect_anomalies + annotate_messages + annotation_report
# ============================================================

@mcp.tool()
def annotate(
    command: str,
    threads_dir: str = "",
    dry_run: bool = True,
) -> dict:
    """标注工具：detect (检测异常), apply (应用标注), report (生成报告)."""
    path = _validate_threads_dir(threads_dir)
    messages = [msg for _, msg in _load_raw_messages(path)]

    if command == "detect":
        ss = detect_same_second_anomalies(messages)
        rs = detect_rapid_succession_batches(messages)
        return {
            "same_second_anomalies": len(ss),
            "rapid_succession_batches": len(rs),
        }

    elif command == "apply":
        result = annotate_all(path, dry_run=dry_run)
        return result.to_dict() | {"dry_run": dry_run}

    elif command == "report":
        result = annotate_all(path, dry_run=True)
        buf = io.StringIO()
        print_report(result, file=buf)
        return {"report": buf.getvalue()}

    else:
        raise ValueError(f"unknown annotate command: {command!r}")


# ============================================================
# Consolidated: constraint — check + list
# ============================================================

@mcp.tool()
def constraint(
    command: str,
    caller: str = "",
    member: str | None = None,
    db_path: str | None = None,
) -> dict:
    """约束文件工具：check (检查哈希变更), list (查看注册表)."""
    if caller:
        _validate_identity(caller)
    bus = _get_bus(db_path)

    if command == "check":
        from lingmessage.constraint_hash import check_and_alert
        with _bus_lock:
            result = check_and_alert(bus)
        return {"changes": result}

    elif command == "list":
        from lingmessage.constraint_hash import create_hash_registry_table, get_current_hashes
        with _bus_lock:
            create_hash_registry_table(bus)
            hashes = get_current_hashes(bus, member=member)
        return {"registrations": hashes}

    else:
        raise ValueError(f"unknown constraint command: {command!r}")


# ============================================================
# SDT Registry — register + list + status
# ============================================================

@mcp.tool()
def sdt_registry(
    command: str,
    member: str = "",
    sdt_id: str = "",
    name: str = "",
    description: str = "",
    direction: str = "",
    priority: str = "P2",
    interval_minutes: int = 1440,
    risk_level: str = "low",
    type: str = "delivery",
    exit_condition: str = "",
    external_verification: str = "",
    status: str = "active",
    enabled: bool = True,
    caller: str = "",
    db_path: str | None = None,
) -> dict:
    """SDT 注册表工具：register (注册/更新), list (查看注册表), status (健康度统计)."""
    if caller:
        _validate_identity(caller)
    bus = _get_bus(db_path)

    from lingmessage.sdt_registry import SDTEntry, get_sdt_stats, list_sdts, register_sdt

    with _bus_lock:
        if command == "register":
            entry = SDTEntry(
                member=member,
                sdt_id=sdt_id,
                name=name,
                description=description,
                direction=direction,
                priority=priority,
                interval_minutes=interval_minutes,
                risk_level=risk_level,
                type=type,
                exit_condition=exit_condition,
                external_verification=external_verification,
                status=status,
                enabled=enabled,
            )
            register_sdt(bus, entry)
            return {"registered": f"{member}/{sdt_id}"}

        elif command == "list":
            entries = list_sdts(bus, member=member or None, status=status if status != "all" else None)
            return {"sdt_entries": entries}

        elif command == "status":
            stats = get_sdt_stats(bus, member=member or None)
            return {"stats": stats}

        else:
            raise ValueError(f"unknown sdt_registry command: {command!r}")


if __name__ == "__main__":
    try:
        from lingmessage.registry import register_fastmcp_server
        register_fastmcp_server("lingmessage-bus", "灵信·统一", mcp, "消息总线+签名+标注")
    except Exception:
        pass
    mcp.run()
