#!/usr/bin/env python3
"""灵信启动巡检 — 自动执行SDT-1~5并记录到注册表。

用法: python3 scripts/sdt_startup.py [--skip SDT_ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingmessage.lingbus import LingBus
from lingmessage.sdt_registry import log_execution, update_sdt_run


def run_sdt_001(bus: LingBus) -> tuple[str, str, float]:
    r = subprocess.run(
        [sys.executable, "-m", "lingmessage.cli", "health"],
        capture_output=True, text=True,
    )
    ok = "系统健康" in r.stdout
    result = "success" if ok else "failed"
    return result, r.stdout[:200], 0.0


def run_sdt_003(bus: LingBus) -> tuple[str, str, float]:
    t0 = time.time()
    r = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
    ports_ok = "9529" in r.stdout and "8765" in r.stdout
    result = "success" if ports_ok else "failed"
    detail = "9529=ok 8765=ok" if ports_ok else "port missing"
    return result, detail, time.time() - t0


def run_sdt_004(bus: LingBus) -> tuple[str, str, float]:
    t0 = time.time()
    from lingmessage.constraint_hash import check_and_alert
    drift = check_and_alert(bus)
    result = "success" if len(drift) == 0 else "warning"
    detail = f"{len(drift)} files drifted" if drift else "no drift"
    return result, detail, time.time() - t0


def run_sdt_005(bus: LingBus) -> tuple[str, str, float]:
    t0 = time.time()
    import sqlite3
    conn = sqlite3.connect(bus._db_path)
    cur = conn.execute(
        "SELECT count(*) FROM threads WHERE channel='governance' AND status='active'"
    )
    gov_count = cur.fetchone()[0]
    conn.close()
    return "success", f"{gov_count} active proposals", time.time() - t0


SDT_TASKS = {
    "SDT-lm-001": ("LingBus健康巡检", run_sdt_001),
    "SDT-lm-003": ("邻居端口巡检", run_sdt_003),
    "SDT-lm-004": ("配置漂移检测", run_sdt_004),
    "SDT-lm-005": ("治理提案巡检", run_sdt_005),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="灵信启动巡检")
    parser.add_argument("--skip", nargs="*", default=[], help="跳过的SDT ID")
    parser.add_argument("--dry-run", action="store_true", help="只执行不记录")
    args = parser.parse_args()

    bus = LingBus()
    skip = set(args.skip)
    results: list[str] = []

    for sdt_id, (name, runner) in SDT_TASKS.items():
        if sdt_id in skip:
            results.append(f"⏭️  {sdt_id} {name}: skipped")
            continue

        t0 = time.time()
        try:
            result, detail, _ = runner(bus)
        except Exception as e:
            result, detail = "failed", str(e)

        duration_total = time.time() - t0

        if not args.dry_run:
            update_sdt_run(bus, "lingmessage", sdt_id, result=result, duration_s=duration_total)
            log_execution(bus, "lingmessage", sdt_id, result=result,
                          duration_s=duration_total, log_type="startup", detail=detail)

        icon = "✅" if result == "success" else "⚠️" if result == "warning" else "❌"
        results.append(f"{icon} {sdt_id} {name}: {result} ({duration_total:.1f}s) {detail}")

    bus.close()

    print("灵信启动巡检报告")
    print("=" * 50)
    for r in results:
        print(f"  {r}")
    print("=" * 50)


if __name__ == "__main__":
    main()
