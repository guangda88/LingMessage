"""灵克情报适配器 — 将灵克情报摘要桥接到灵信邮箱"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    LingIdentity,
    MessageType,
    SourceType,
    create_message,
)


class lingclaudeIntelAdapter:
    """灵克情报 → 灵信 shared-infra 频道"""

    def __init__(self, mailbox: Mailbox, lingclaude_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingclaude_root or Path(os.environ.get("LINGCLAUDE_ROOT", "/home/ai/lingclaude"))

    def _intel_dir(self) -> Path:
        return self._root / ".lingclaude" / "intel"

    def post_digests(self) -> list[str]:
        posted: list[str] = []
        intel_dir = self._intel_dir()
        if not intel_dir.exists():
            return posted
        for dp in sorted(intel_dir.glob("digest_*.json")):
            try:
                data = json.loads(dp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            report_date = data.get("report_date", "")
            summary_text = data.get("summary", "")
            findings = data.get("key_findings", [])
            recommendations = data.get("recommendations", [])
            body_parts: list[str] = [f"## 灵克情报摘要 {report_date}\n"]
            if summary_text:
                body_parts.append(f"**概要**: {summary_text}\n")
            if findings:
                body_parts.append("**关键发现**:")
                for f in findings[:5]:
                    body_parts.append(f"  - {f}")
                body_parts.append("")
            if recommendations:
                body_parts.append("**建议**:")
                for r in recommendations[:5]:
                    body_parts.append(f"  - {r}")
                body_parts.append("")
            cat_counts = data.get("category_counts", {})
            if cat_counts:
                body_parts.append(f"**类别分布**: {cat_counts}")
            body = "\n".join(body_parts)
            msg = create_message(
                sender=LingIdentity.LINGCLAUDE,
                recipient=LingIdentity.ALL,
                message_type=MessageType.SUMMARY,
                channel=Channel.SHARED_INFRA,
                subject=f"灵克情报摘要 {report_date}",
                body=body,
                metadata={"source": "daily_digest", "date": report_date},
                source_type=SourceType.GENERATED,
                source_trace=f"lingclaude:adapter:digest:{report_date}",
            )
            self._mailbox.post(msg)
            posted.append(msg.message_id)
        return posted


def get_lingclaude_intel_adapter(mailbox: Mailbox, **kwargs) -> lingclaudeIntelAdapter:
    return lingclaudeIntelAdapter(mailbox, **kwargs)
