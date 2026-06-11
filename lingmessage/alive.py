"""灵信存活检测 — 检查灵族成员的真实存活状态"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingmessage.types import LingIdentity, _IDENTITY_NAMES


_DIR_MAP: dict[str, str] = {
    "lingflow": "lingflow",
    "lingclaude": "lingclaude",
    "lingyi": "lingyi",
    "lingzhi": "zhineng-knowledge-system",
    "lingtongask": "lingtongask",
    "lingxi": "Ling-term-mcp",
    "lingminopt": "lingminopt",
    "lingresearch": "lingresearch",
    "lingyang": "lingyang",
    "zhibridge": "zhineng-bridge",
    "lingmessage": "lingmessage",
    "lingweb": "lingweb",
}


@dataclass(frozen=True)
class AliveStatus:
    identity: str
    display_name: str
    project_dir: str
    dir_exists: bool
    is_git: bool
    last_commit_time: str
    last_commit_hash: str
    age_hours: float
    commits_7d: int
    status: str  # active, recent, silent, missing, not_git

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "display_name": self.display_name,
            "project_dir": self.project_dir,
            "dir_exists": self.dir_exists,
            "is_git": self.is_git,
            "last_commit_time": self.last_commit_time,
            "last_commit_hash": self.last_commit_hash,
            "age_hours": round(self.age_hours, 1),
            "commits_7d": self.commits_7d,
            "status": self.status,
        }


def check_member(member: str, home: Path | None = None) -> AliveStatus:
    home = home or Path.home()
    dirname = _DIR_MAP.get(member, "")
    display = _IDENTITY_NAMES.get(LingIdentity(member), member)

    if not dirname:
        return AliveStatus(
            identity=member, display_name=display, project_dir="",
            dir_exists=False, is_git=False, last_commit_time="",
            last_commit_hash="", age_hours=-1, commits_7d=0, status="missing",
        )

    d = home / dirname
    if not d.exists():
        return AliveStatus(
            identity=member, display_name=display, project_dir=dirname,
            dir_exists=False, is_git=False, last_commit_time="",
            last_commit_hash="", age_hours=-1, commits_7d=0, status="missing",
        )

    is_git = (d / ".git").exists()
    if not is_git:
        return AliveStatus(
            identity=member, display_name=display, project_dir=dirname,
            dir_exists=True, is_git=False, last_commit_time="",
            last_commit_hash="", age_hours=-1, commits_7d=0, status="not_git",
        )

    now = datetime.now(timezone.utc)
    last_time = ""
    last_hash = ""
    age_hours = -1.0
    commits_7d = 0

    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%H %ci"],
            cwd=str(d), capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(" ", 1)
            last_hash = parts[0][:8]
            last_time = parts[1][:25]
            ct = datetime.fromisoformat(last_time)
            age_hours = (now - ct).total_seconds() / 3600
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "--since=7 days ago"],
            cwd=str(d), capture_output=True, text=True, timeout=5,
        )
        commits_7d = len([ln for ln in r.stdout.strip().split('\n') if ln.strip()])
    except Exception:
        pass

    if age_hours < 0:
        status = "unknown"
    elif age_hours < 24:
        status = "active"
    elif age_hours < 72:
        status = "recent"
    elif age_hours < 168:
        status = "silent"
    else:
        status = "offline"

    return AliveStatus(
        identity=member, display_name=display, project_dir=dirname,
        dir_exists=True, is_git=True, last_commit_time=last_time,
        last_commit_hash=last_hash, age_hours=age_hours,
        commits_7d=commits_7d, status=status,
    )


def check_all(home: Path | None = None) -> list[AliveStatus]:
    results = []
    for member in _DIR_MAP:
        if member == "lingmessage":
            continue
        results.append(check_member(member, home))
    return results


_STATUS_ICONS = {
    "active": "🟢",
    "recent": "🟡",
    "silent": "🟠",
    "offline": "🔴",
    "missing": "❌",
    "not_git": "⚠️ ",
    "unknown": "❓",
}


def format_report(results: list[AliveStatus], verbose: bool = False) -> str:
    lines = ["=== 灵族存活状态 ===\n"]

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    for r in sorted(results, key=lambda x: x.age_hours):
        icon = _STATUS_ICONS.get(r.status, "❓")
        name = f"{r.identity:<14}"
        dir_info = f"({r.project_dir})" if verbose else ""

        if r.status in ("missing", "not_git"):
            lines.append(f"{icon} {name} {r.status}{dir_info}")
        elif verbose:
            lines.append(
                f"{icon} {name} {r.last_commit_time} ({r.age_hours:.0f}h) "
                f"7d:{r.commits_7d} {dir_info}"
            )
        else:
            lines.append(f"{icon} {name} {r.age_hours:.0f}h前  7日提交: {r.commits_7d}")

    lines.append(f"\n总计: {len(results)} 成员")
    active_count = by_status.get("active", 0)
    lines.append(f"活跃: {active_count}  近期: {by_status.get('recent', 0)}  "
                 f"静默: {by_status.get('silent', 0)}  失联: {by_status.get('offline', 0)}")

    now = datetime.now(timezone.utc)
    lines.append(f"\n生成时间: {now.isoformat()}")
    return "\n".join(lines)
