"""灵信 SDT 注册表 — 集中管理全族自驱任务

在 LingBus DB 中维护 sdt_registry + sdt_exec_log 表，提供注册、查询、
状态更新、健康度统计、执行记录、stale检测等功能。
遵循 SESSION_LIFECYCLE_PROTOCOL.md §6 规范 + 灵克审计建议(P1/P2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingmessage.lingbus import LingBus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SDT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sdt_registry (
    member              TEXT NOT NULL,
    sdt_id              TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    direction           TEXT NOT NULL DEFAULT '',
    priority            TEXT NOT NULL DEFAULT 'P2',
    interval_minutes    INTEGER NOT NULL DEFAULT 1440,
    risk_level          TEXT NOT NULL DEFAULT 'low',
    type                TEXT NOT NULL DEFAULT 'delivery',
    exit_condition      TEXT NOT NULL DEFAULT '',
    external_verification TEXT NOT NULL DEFAULT '',
    sdt_version         TEXT NOT NULL DEFAULT '0.1.0',
    verifier            TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    enabled             INTEGER NOT NULL DEFAULT 1,
    last_run            TEXT NOT NULL DEFAULT '',
    last_result         TEXT NOT NULL DEFAULT '',
    consecutive_runs    INTEGER NOT NULL DEFAULT 0,
    registered_at       TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (member, sdt_id)
)
"""

_EXEC_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sdt_exec_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member      TEXT NOT NULL,
    sdt_id      TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT 'success',
    duration_s  REAL NOT NULL DEFAULT 0.0,
    log_type    TEXT NOT NULL DEFAULT 'execution',
    detail      TEXT NOT NULL DEFAULT ''
)
"""

_STALE_MISSED_THRESHOLD = 3  # consecutive missed runs → stale


@dataclass
class SDTEntry:
    member: str
    sdt_id: str
    name: str = ""
    description: str = ""
    direction: str = ""
    priority: str = "P2"
    interval_minutes: int = 1440
    risk_level: str = "low"
    type: str = "delivery"
    exit_condition: str = ""
    external_verification: str = ""
    sdt_version: str = "0.1.0"
    verifier: str = ""
    status: str = "active"
    enabled: bool = True
    last_run: str = ""
    last_result: str = ""
    consecutive_runs: int = 0
    registered_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "member": self.member,
            "sdt_id": self.sdt_id,
            "name": self.name,
            "description": self.description,
            "direction": self.direction,
            "priority": self.priority,
            "interval_minutes": self.interval_minutes,
            "risk_level": self.risk_level,
            "type": self.type,
            "exit_condition": self.exit_condition,
            "external_verification": self.external_verification,
            "sdt_version": self.sdt_version,
            "verifier": self.verifier,
            "status": self.status,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "consecutive_runs": self.consecutive_runs,
            "registered_at": self.registered_at,
            "updated_at": self.updated_at,
        }


def _create_tables(bus: LingBus) -> None:
    bus.ensure_table(_SDT_TABLE_SQL)
    bus.ensure_table(_EXEC_LOG_TABLE_SQL)


def add_columns(bus: LingBus) -> None:
    """Add new columns for existing databases (idempotent)."""
    _create_tables(bus)
    for col, col_type in [("sdt_version", "TEXT"), ("verifier", "TEXT")]:
        try:
            bus.execute_write(
                f"ALTER TABLE sdt_registry ADD COLUMN {col} {col_type} NOT NULL DEFAULT ''"
            )
        except Exception:
            pass  # column already exists


def create_sdt_registry_table(bus: LingBus) -> None:
    """Create both registry and exec_log tables."""
    _create_tables(bus)


def register_sdt(bus: LingBus, entry: SDTEntry) -> None:
    """Register or update an SDT entry."""
    create_sdt_registry_table(bus)
    add_columns(bus)
    now = _now_iso()
    bus.execute_write(
        "INSERT OR REPLACE INTO sdt_registry "
        "(member, sdt_id, name, description, direction, priority, "
        " interval_minutes, risk_level, type, exit_condition, "
        " external_verification, sdt_version, verifier, status, enabled, "
        " last_run, last_result, consecutive_runs, registered_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, "
        "        ?, ?, ?, ?, "
        "        ?, ?, ?, ?, ?, "
        "        ?, ?, ?, "
        "        COALESCE((SELECT registered_at FROM sdt_registry "
        "                  WHERE member=? AND sdt_id=?), ?), ?)",
        (
            entry.member,
            entry.sdt_id,
            entry.name,
            entry.description,
            entry.direction,
            entry.priority,
            entry.interval_minutes,
            entry.risk_level,
            entry.type,
            entry.exit_condition,
            entry.external_verification,
            entry.sdt_version,
            entry.verifier,
            entry.status,
            1 if entry.enabled else 0,
            entry.last_run,
            entry.last_result,
            entry.consecutive_runs,
            entry.member,
            entry.sdt_id,
            now,
            now,
        ),
    )
    logger.info("SDT registered: %s/%s v%s", entry.member, entry.sdt_id, entry.sdt_version)


def log_execution(bus: LingBus, member: str, sdt_id: str,
                  *, result: str = "success", duration_s: float = 0.0,
                  log_type: str = "execution", detail: str = "") -> None:
    """Record an SDT execution to the exec log."""
    create_sdt_registry_table(bus)
    bus.execute_write(
        "INSERT INTO sdt_exec_log (member, sdt_id, executed_at, result, duration_s, log_type, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (member, sdt_id, _now_iso(), result, duration_s, log_type, detail),
    )


def update_sdt_run(bus: LingBus, member: str, sdt_id: str,
                   *, result: str = "success", increment_runs: bool = True,
                   duration_s: float = 0.0) -> None:
    """Update the last_run timestamp and result for an SDT, and log execution."""
    create_sdt_registry_table(bus)
    now = _now_iso()
    if increment_runs:
        bus.execute_write(
            "UPDATE sdt_registry SET last_run=?, last_result=?, "
            "  consecutive_runs=consecutive_runs+1, updated_at=? "
            "WHERE member=? AND sdt_id=?",
            (now, result, now, member, sdt_id),
        )
    else:
        bus.execute_write(
            "UPDATE sdt_registry SET last_run=?, last_result=?, "
            "  consecutive_runs=0, updated_at=? "
            "WHERE member=? AND sdt_id=?",
            (now, result, now, member, sdt_id),
        )
    log_execution(bus, member, sdt_id, result=result, duration_s=duration_s)


def list_sdts(bus: LingBus, member: str | None = None,
              status: str | None = None) -> list[dict]:
    """List SDT entries, optionally filtered by member and/or status."""
    create_sdt_registry_table(bus)
    add_columns(bus)
    conditions: list[str] = []
    params: list[str] = []
    if member:
        conditions.append("member=?")
        params.append(member)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = bus.execute_readonly(
        f"SELECT * FROM sdt_registry {where} ORDER BY member, sdt_id",
        tuple(params),
    )
    return [
        {
            "member": r["member"],
            "sdt_id": r["sdt_id"],
            "name": r["name"],
            "description": r["description"],
            "direction": r["direction"],
            "priority": r["priority"],
            "interval_minutes": r["interval_minutes"],
            "risk_level": r["risk_level"],
            "type": r["type"],
            "exit_condition": r["exit_condition"],
            "external_verification": r["external_verification"],
            "sdt_version": r["sdt_version"] if "sdt_version" in r.keys() else "",
            "verifier": r["verifier"] if "verifier" in r.keys() else "",
            "status": r["status"],
            "enabled": bool(r["enabled"]),
            "last_run": r["last_run"],
            "last_result": r["last_result"],
            "consecutive_runs": r["consecutive_runs"],
            "registered_at": r["registered_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def get_exec_log(bus: LingBus, member: str | None = None,
                 sdt_id: str | None = None, limit: int = 50) -> list[dict]:
    """Get execution log entries, optionally filtered."""
    create_sdt_registry_table(bus)
    conditions: list[str] = []
    params: list[str] = []
    if member:
        conditions.append("member=?")
        params.append(member)
    if sdt_id:
        conditions.append("sdt_id=?")
        params.append(sdt_id)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = bus.execute_readonly(
        f"SELECT * FROM sdt_exec_log {where} ORDER BY executed_at DESC LIMIT ?",
        tuple(params) + (limit,),
    )
    return [
        {
            "id": r["id"],
            "member": r["member"],
            "sdt_id": r["sdt_id"],
            "executed_at": r["executed_at"],
            "result": r["result"],
            "duration_s": r["duration_s"],
            "log_type": r["log_type"],
            "detail": r["detail"],
        }
        for r in rows
    ]


def check_stale(bus: LingBus) -> list[dict]:
    """Check for stale SDTs (missed runs) and auto-mark them.

    SDT-5 integration: scan all active SDTs, if last_run is older than
    2× interval_minutes, mark as stale_unused after _STALE_MISSED_THRESHOLD
    consecutive misses.

    Returns list of stale entries found.
    """
    create_sdt_registry_table(bus)
    add_columns(bus)
    now = datetime.now(timezone.utc)
    stale_entries: list[dict] = []

    rows = bus.execute_readonly(
        "SELECT * FROM sdt_registry WHERE status='active' AND enabled=1",
    )
    for r in rows:
        if not r["last_run"]:
            continue
        interval = r["interval_minutes"] or 1440
        try:
            last = datetime.fromisoformat(r["last_run"])
        except (ValueError, TypeError):
            continue
        elapsed = (now - last).total_seconds() / 60
        # If 2x interval passed without a run, it's a missed run
        if elapsed > interval * 2:
            # Count how many consecutive missed runs
            misses = int(elapsed / interval)
            if misses >= _STALE_MISSED_THRESHOLD:
                log_execution(bus, r["member"], r["sdt_id"],
                              result="missed", log_type="stale",
                              detail=f"auto-stale: {misses}x interval elapsed ({elapsed:.0f}m)")
                bus.execute_write(
                    "UPDATE sdt_registry SET status='stale', updated_at=? "
                    "WHERE member=? AND sdt_id=?",
                    (_now_iso(), r["member"], r["sdt_id"]),
                )
                stale_entries.append({
                    "member": r["member"],
                    "sdt_id": r["sdt_id"],
                    "name": r["name"],
                    "elapsed_minutes": round(elapsed),
                    "missed_runs": misses,
                    "action": "marked_stale",
                })
                logger.warning("SDT stale: %s/%s (%s) — %d missed runs",
                               r["member"], r["sdt_id"], r["name"], misses)
    return stale_entries


def get_sdt_stats(bus: LingBus, member: str | None = None) -> dict:
    """Compute SDT health statistics.

    Returns:
        dict with keys:
          - total: total registered SDTs
          - active: count of active SDTs
          - stale: count of stale SDTs
          - enabled: count of enabled SDTs
          - execution_rate: ratio of SDTs with last_run not empty
          - success_rate: ratio of active SDTs where last_result == 'success'
          - external_verification_rate: ratio with non-empty external_verification
          - versioned_rate: ratio with non-empty sdt_version
          - stale_rate: ratio of stale / total
          - by_member: per-member breakdown (if member is None)
    """
    create_sdt_registry_table(bus)
    all_sdts = list_sdts(bus, member=member)
    total = len(all_sdts)
    if total == 0:
        return {"total": 0, "active": 0, "stale": 0, "enabled": 0,
                "execution_rate": 0.0, "success_rate": 0.0,
                "external_verification_rate": 0.0,
                "versioned_rate": 0.0, "stale_rate": 0.0}

    active = [s for s in all_sdts if s["status"] == "active"]
    stale = [s for s in all_sdts if s["status"] == "stale"]
    enabled = [s for s in all_sdts if s["enabled"]]
    executed = [s for s in all_sdts if s["last_run"]]
    succeeded = [s for s in active if s["last_result"] == "success"]
    ext_verified = [s for s in all_sdts if s["external_verification"]]
    versioned = [s for s in all_sdts if s.get("sdt_version")]

    stats = {
        "total": total,
        "active": len(active),
        "stale": len(stale),
        "enabled": len(enabled),
        "execution_rate": round(len(executed) / total, 3),
        "success_rate": round(len(succeeded) / len(active), 3) if active else 0.0,
        "external_verification_rate": round(len(ext_verified) / total, 3),
        "versioned_rate": round(len(versioned) / total, 3),
        "stale_rate": round(len(stale) / total, 3),
    }

    if not member:
        by_member: dict[str, dict] = {}
        for s in all_sdts:
            m = s["member"]
            if m not in by_member:
                by_member[m] = {"total": 0, "active": 0, "stale": 0,
                                "executed": 0, "succeeded": 0,
                                "ext_verified": 0, "versioned": 0}
            by_member[m]["total"] += 1
            if s["status"] == "active":
                by_member[m]["active"] += 1
            if s["status"] == "stale":
                by_member[m]["stale"] += 1
            if s["last_run"]:
                by_member[m]["executed"] += 1
            if s["last_result"] == "success":
                by_member[m]["succeeded"] += 1
            if s["external_verification"]:
                by_member[m]["ext_verified"] += 1
            if s.get("sdt_version"):
                by_member[m]["versioned"] += 1
        stats["by_member"] = by_member

    return stats
