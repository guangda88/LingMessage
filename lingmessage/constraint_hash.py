"""灵信约束文件哈希校验 — 监控 CRUSH.md / AGENTS.md 变更并告警

为灵族各成员的约束文件（CRUSH.md, AGENTS.md 等）计算 SHA-256 哈希，
记录到 LingBus 的 hash_registry 表中。当哈希值发生变化时，
自动发送告警到 governance 通道。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingmessage.lingbus import LingBus

logger = logging.getLogger(__name__)

_CONSTRAINT_FILES = ("CRUSH.md", "AGENTS.md")
_BASE_DIR = Path.home()


def discover_member_dirs() -> dict[str, str]:
    """Discover member directories by scanning /home/ai/ for CRUSH.md files.

    Any subdirectory of _BASE_DIR containing a CRUSH.md or AGENTS.md
    is considered a member directory. The directory name is used as the
    member identity.

    Returns:
        Dict mapping member identity to directory path
    """
    dirs: dict[str, str] = {}
    if not _BASE_DIR.exists():
        return dirs
    for child in sorted(_BASE_DIR.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name.startswith("_"):
            continue
        try:
            has_constraint = any((child / fname).exists() for fname in _CONSTRAINT_FILES)
        except PermissionError:
            continue
        if has_constraint:
            dirs[child.name] = str(child)
    return dirs


_MEMBER_DIRS: dict[str, str] = discover_member_dirs()

_CONSTRAINT_FILES = ("CRUSH.md", "AGENTS.md")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file. Returns empty string if file missing."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@dataclass
class HashEntry:
    member: str
    filename: str
    hash_sha256: str
    file_path: str
    recorded_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "member": self.member,
            "filename": self.filename,
            "hash_sha256": self.hash_sha256,
            "file_path": self.file_path,
            "recorded_at": self.recorded_at,
        }


def create_hash_registry_table(bus: LingBus) -> None:
    """Create the hash_registry table in LingBus database."""
    bus.ensure_table("""
        CREATE TABLE IF NOT EXISTS hash_registry (
            member       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            hash_sha256  TEXT NOT NULL,
            file_path    TEXT NOT NULL,
            recorded_at  TEXT NOT NULL,
            PRIMARY KEY (member, filename)
        )
    """)


def snapshot_member(member: str, member_dir: str | Path | None = None) -> list[HashEntry]:
    """Compute hashes for a member's constraint files.

    Args:
        member: Member identity (e.g. 'lingflow')
        member_dir: Override directory path

    Returns:
        List of HashEntry objects
    """
    base = Path(member_dir) if member_dir else Path(_MEMBER_DIRS.get(member, ""))
    entries: list[HashEntry] = []
    for fname in _CONSTRAINT_FILES:
        fpath = base / fname
        h = file_sha256(fpath)
        if h:
            entries.append(HashEntry(
                member=member,
                filename=fname,
                hash_sha256=h,
                file_path=str(fpath),
                recorded_at=_now_iso(),
            ))
    return entries


def snapshot_all() -> list[HashEntry]:
    """Snapshot all members' constraint files."""
    entries: list[HashEntry] = []
    for member in _MEMBER_DIRS:
        entries.extend(snapshot_member(member))
    return entries


def record_hashes(bus: LingBus, entries: list[HashEntry]) -> int:
    """Record hash entries to the registry. Returns number recorded."""
    count = 0
    for e in entries:
        bus.execute_write(
            "INSERT OR REPLACE INTO hash_registry (member, filename, hash_sha256, file_path, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (e.member, e.filename, e.hash_sha256, e.file_path, e.recorded_at),
        )
        count += 1
    return count


def detect_changes(bus: LingBus, entries: list[HashEntry]) -> list[dict[str, str]]:
    """Compare entries against registry. Returns list of changes.

    Each change dict has: member, filename, old_hash, new_hash, file_path
    """
    changes: list[dict[str, str]] = []
    for e in entries:
        rows = bus.execute_readonly(
            "SELECT hash_sha256 FROM hash_registry WHERE member = ? AND filename = ?",
            (e.member, e.filename),
        )
        row = rows[0] if rows else None
        if row is None:
            changes.append({
                "member": e.member,
                "filename": e.filename,
                "old_hash": "",
                "new_hash": e.hash_sha256,
                "file_path": e.file_path,
                "change_type": "new",
            })
        elif row["hash_sha256"] != e.hash_sha256:
            changes.append({
                "member": e.member,
                "filename": e.filename,
                "old_hash": row["hash_sha256"],
                "new_hash": e.hash_sha256,
                "file_path": e.file_path,
                "change_type": "modified",
            })
    return changes


def check_and_alert(bus: LingBus) -> list[dict[str, str]]:
    """Full check cycle: snapshot, compare, record, and alert."""
    create_hash_registry_table(bus)
    entries = snapshot_all()
    changes = detect_changes(bus, entries)
    record_hashes(bus, entries)

    modifications = [c for c in changes if c["change_type"] == "modified"]
    if modifications:
        parts: list[str] = ["## 约束文件哈希变更告警\n"]
        for c in modifications:
            parts.append(f"- **{c['member']}/{c['filename']}** 变更")
            parts.append(f"  旧: `{c['old_hash'][:16]}...`")
            parts.append(f"  新: `{c['new_hash'][:16]}...`")
        parts.append(f"\n共 {len(modifications)} 项变更 @ {_now_iso()}")

        try:
            bus.open_thread(
                topic="约束文件哈希变更告警",
                sender="lingmessage",
                recipients=["all"],
                channel="alert",
                subject="约束文件哈希变更",
                body="\n".join(parts),
            )
            logger.info("约束文件哈希告警已发送: %d 项变更", len(changes))
        except Exception as e:
            logger.error("发送哈希告警失败: %s", e)

    return changes


def get_current_hashes(bus: LingBus, member: str | None = None) -> list[dict[str, str]]:
    """Get current recorded hashes from the registry."""
    if member:
        rows = bus.execute_readonly(
            "SELECT * FROM hash_registry WHERE member = ? ORDER BY filename",
            (member,),
        )
    else:
        rows = bus.execute_readonly(
            "SELECT * FROM hash_registry ORDER BY member, filename"
        )
    return [
        {
            "member": r["member"],
            "filename": r["filename"],
            "hash_sha256": r["hash_sha256"],
            "file_path": r["file_path"],
            "recorded_at": r["recorded_at"],
        }
        for r in rows
    ]
