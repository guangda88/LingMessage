"""灵信红区操作审批 — 通过治理引擎管理红区操作

红区操作（杀进程/删数据/改配置/改约束文件等）必须经过审批流程：
1. 调用 require_approval() 发起治理提案
2. 其他成员投票（approve/reject/abstain）
3. 达到法定人数且多数赞成才能执行

红区操作定义：
- 杀死灵族成员进程
- 删除数据库/数据文件
- 修改 CRUSH.md / AGENTS.md 约束文件
- 修改基础设施配置（proxy/systemd/端口）
- 超出预算的支出
- 修改灵族成员名单
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Zone(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class RedZoneCategory(str, Enum):
    KILL_PROCESS = "kill_process"
    DELETE_DATA = "delete_data"
    MODIFY_CONSTRAINT = "modify_constraint"
    MODIFY_INFRA = "modify_infra"
    BUDGET_EXCEED = "budget_exceed"
    MODIFY_MEMBERSHIP = "modify_membership"
    OTHER = "other"


_RED_ZONE_DESCRIPTIONS: dict[RedZoneCategory, str] = {
    RedZoneCategory.KILL_PROCESS: "杀死灵族成员进程",
    RedZoneCategory.DELETE_DATA: "删除数据库/数据文件",
    RedZoneCategory.MODIFY_CONSTRAINT: "修改约束文件(CRUSH.md/AGENTS.md)",
    RedZoneCategory.MODIFY_INFRA: "修改基础设施配置",
    RedZoneCategory.BUDGET_EXCEED: "超出预算支出",
    RedZoneCategory.MODIFY_MEMBERSHIP: "修改灵族成员名单",
    RedZoneCategory.OTHER: "其他红区操作",
}


@dataclass
class ApprovalRequest:
    requester: str
    category: RedZoneCategory
    reason: str
    target: str
    user_message: str = ""

    def to_body(self) -> str:
        desc = _RED_ZONE_DESCRIPTIONS.get(self.category, self.category.value)
        parts = [
            "## 红区操作审批请求\n",
            f"**请求者**: {self.requester}",
            f"**操作类型**: {desc}",
            f"**目标**: {self.target}",
            f"**理由**: {self.reason}",
        ]
        if self.user_message:
            parts.append(f"\n**用户消息**: {self.user_message}")
        parts.append("\n---")
        parts.append("本提案为红区操作审批。请各成员审慎投票。")
        return "\n".join(parts)


def classify_zone(operation: str) -> Zone:
    """Classify an operation into a zone.

    Args:
        operation: Description of the operation

    Returns:
        Zone classification (GREEN, YELLOW, or RED)
    """
    red_keywords = [
        "kill", "杀死", "终止进程",
        "delete", "删除数据", "rm ",
        "CRUSH.md", "AGENTS.md", "约束文件",
        "proxy_config", "systemd", "端口",
        "预算", "budget", "支出",
        "成员名单", "membership",
    ]
    yellow_keywords = [
        "restart", "重启",
        "config", "配置",
        "deploy", "部署",
        "update", "更新",
    ]

    op_lower = operation.lower()
    for kw in red_keywords:
        if kw.lower() in op_lower:
            return Zone.RED
    for kw in yellow_keywords:
        if kw.lower() in op_lower:
            return Zone.YELLOW
    return Zone.GREEN


def require_approval(
    bus_or_mailbox: Any,
    *,
    requester: str,
    category: RedZoneCategory,
    reason: str,
    target: str,
    user_message: str = "",
    recipients: list[str] | None = None,
    quorum: int = 2,
    deadline_hours: int = 24,
) -> dict[str, str]:
    """Initiate a red-zone approval request via governance.

    Creates a governance proposal thread. The operation should not proceed
    until the proposal is approved via the standard vote → resolve flow.

    Args:
        bus_or_mailbox: LingBus or Mailbox instance
        requester: Member identity requesting the operation
        category: Red zone category
        reason: Why the operation is needed
        target: What will be affected
        user_message: Optional message from user triggering this
        recipients: Override recipients (default: all)
        quorum: Minimum votes required (default: 2, minimum: 1)
        deadline_hours: Voting deadline in hours (default: 24)

    Returns:
        {"thread_id": str, "message_id": str} of the proposal
    """
    if quorum < 1:
        raise ValueError("quorum must be >= 1")
    req = ApprovalRequest(
        requester=requester,
        category=category,
        reason=reason,
        target=target,
        user_message=user_message,
    )

    desc = _RED_ZONE_DESCRIPTIONS.get(category, category.value)
    topic = f"红区审批: {desc} — {target[:50]}"
    body = req.to_body()
    recips = recipients or ["all"]

    if hasattr(bus_or_mailbox, "open_thread"):
        result = bus_or_mailbox.open_thread(
            topic=topic,
            sender=requester,
            recipients=recips,
            channel="governance",
            subject=f"红区审批: {desc}",
            body=body,
        )
        if isinstance(result, tuple) and len(result) == 2:
            thread_id, message_id = result
        else:
            thread_id = ""
            message_id = str(result)
        logger.info(
            "红区审批已发起: requester=%s category=%s target=%s thread=%s",
            requester, category.value, target, thread_id,
        )
        return {"thread_id": thread_id, "message_id": message_id}

    raise ValueError("Invalid bus_or_mailbox: must have open_thread method")
