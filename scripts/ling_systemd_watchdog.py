#!/usr/bin/env python3
"""灵信 systemd 服务看门狗

监控灵族 systemd 用户服务的重启次数，超过阈值时通过 LingBus 发送告警。

用法：
    python scripts/ling_systemd_watchdog.py              # 单次扫描
    python scripts/ling_systemd_watchdog.py --interval 300  # 持续监控
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingmessage.lingbus import LingBus

logger = logging.getLogger("lingmessage.watchdog")

STATE_FILE = Path.home() / ".lingmessage" / "watchdog_state.json"

MONITORED_SERVICES: list[str] = [
    "lingmessage-poller.service",
]

DEFAULT_RESTART_THRESHOLD = 3


@dataclass
class ServiceStatus:
    service_name: str
    active_state: str
    nrestarts: int
    result: str
    enter_time: str
    units: str = ""

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service_name,
            "active_state": self.active_state,
            "nrestarts": self.nrestarts,
            "result": self.result,
            "enter_time": self.enter_time,
            "units": self.units,
        }


def query_service(service_name: str) -> ServiceStatus:
    """Query systemd user service properties via systemctl."""
    try:
        r = subprocess.run(
            [
                "systemctl", "--user", "show", service_name,
                "--property=ActiveState,NRestarts,Result,ActiveEnterTimestamp,Names",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"Failed to query {service_name}: {e}")
        return ServiceStatus(
            service_name=service_name, active_state="unknown",
            nrestarts=0, result="unknown", enter_time="",
        )

    if r.returncode != 0:
        logger.warning(f"systemctl returned {r.returncode} for {service_name}: {r.stderr.strip()}")
        return ServiceStatus(
            service_name=service_name, active_state="not-found",
            nrestarts=0, result="not-found", enter_time="",
        )

    props: dict[str, str] = {}
    for line in r.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v

    return ServiceStatus(
        service_name=service_name,
        active_state=props.get("ActiveState", "unknown"),
        nrestarts=int(props.get("NRestarts", "0")),
        result=props.get("Result", "unknown"),
        enter_time=props.get("ActiveEnterTimestamp", ""),
        units=props.get("Names", ""),
    )


class WatchdogState:
    """Persistent state tracking — records last alerted restart count per service."""

    def __init__(self, path: Path | None = None):
        self._path = path or STATE_FILE
        self._alerts: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._alerts = data.get("alerts", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load watchdog state: {e}")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.write_text(
                json.dumps({"alerts": self._alerts}, indent=2, ensure_ascii=False)
            )
        except OSError as e:
            logger.warning(f"Failed to save watchdog state: {e}")

    def last_alerted_restarts(self, service_name: str) -> int:
        return self._alerts.get(service_name, {}).get("last_alerted", 0)

    def record_alert(self, service_name: str, restart_count: int) -> None:
        self._alerts[service_name] = {
            "last_alerted": restart_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def reset_service(self, service_name: str) -> None:
        if service_name in self._alerts:
            del self._alerts[service_name]
            self._save()


def send_alert(bus: LingBus, service_name: str, status: ServiceStatus, threshold: int) -> str | None:
    """Send a LingBus alert for a service exceeding restart threshold."""
    topic = f"看门狗告警：{service_name} 重启 {status.nrestarts} 次"
    body = (
        f"🚨 服务看门狗告警\n\n"
        f"服务：{status.service_name}\n"
        f"当前状态：{status.active_state}\n"
        f"重启次数：{status.nrestarts}（阈值：{threshold}）\n"
        f"上次激活结果：{status.result}\n"
        f"激活时间：{status.enter_time or '未知'}\n\n"
        f"请相关成员检查服务状态。\n\n"
        f"—— 灵信看门狗 ({datetime.now(timezone.utc).isoformat()})"
    )
    try:
        thread_id, msg_id = bus.open_thread(
            topic=topic,
            sender="lingmessage",
            recipients="all",
            channel="ecosystem",
            subject=topic,
            body=body,
        )
        logger.info(f"Alert sent: thread={thread_id} for {service_name}")
        return thread_id
    except Exception as e:
        logger.error(f"Failed to send alert for {service_name}: {e}")
        return None


def scan_once(
    services: list[str] | None = None,
    threshold: int = DEFAULT_RESTART_THRESHOLD,
    state: WatchdogState | None = None,
    bus: LingBus | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Single scan of all monitored services.

    Returns dict with scan results and actions taken.
    """
    services = services or MONITORED_SERVICES
    state = state or WatchdogState()
    bus = bus or LingBus()

    results: list[dict[str, Any]] = []
    alerts: list[str] = []

    for svc in services:
        status = query_service(svc)
        results.append(status.to_dict())
        logger.debug(f"{svc}: state={status.active_state} restarts={status.nrestarts}")

        if not status.is_active:
            logger.warning(f"{svc} is not active: {status.active_state}")
            continue

        last_alerted = state.last_alerted_restarts(svc)
        if status.nrestarts > threshold and status.nrestarts > last_alerted:
            if dry_run:
                alerts.append(f"[DRY-RUN] Would alert: {svc} restarts={status.nrestarts} > {threshold}")
                logger.info(f"[DRY-RUN] Would alert for {svc}")
            else:
                tid = send_alert(bus, svc, status, threshold)
                if tid:
                    state.record_alert(svc, status.nrestarts)
                    alerts.append(f"Alerted: {svc} restarts={status.nrestarts}, thread={tid}")
                else:
                    alerts.append(f"Alert FAILED: {svc}")
        elif status.nrestarts <= threshold and last_alerted > 0:
            state.reset_service(svc)
            logger.info(f"{svc} recovered (restarts={status.nrestarts} <= {threshold}), state reset")

    return {
        "scanned": len(services),
        "results": results,
        "alerts": alerts,
        "threshold": threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="灵信 systemd 服务看门狗")
    parser.add_argument("--once", action="store_true", help="单次扫描后退出（默认）")
    parser.add_argument("--interval", type=int, default=0, help="持续监控间隔（秒）")
    parser.add_argument("--threshold", type=int, default=DEFAULT_RESTART_THRESHOLD,
                        help=f"重启次数告警阈值（默认 {DEFAULT_RESTART_THRESHOLD}）")
    parser.add_argument("--service", action="append", dest="services",
                        help="额外监控的服务（可多次指定）")
    parser.add_argument("--dry-run", action="store_true", help="不发送告警，仅打印")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    services = list(MONITORED_SERVICES)
    if args.services:
        services.extend(args.services)

    interval = args.interval

    if not interval:
        result = scan_once(
            services=services, threshold=args.threshold, dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1 if result["alerts"] else 0)

    logger.info(f"Watchdog started, interval={interval}s, threshold={args.threshold}")
    while True:
        try:
            result = scan_once(
                services=services, threshold=args.threshold, dry_run=args.dry_run,
            )
            if result["alerts"]:
                for alert in result["alerts"]:
                    logger.info(f"  → {alert}")
            else:
                logger.debug(f"Scan complete: {result['scanned']} services, no alerts")
        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)

        time.sleep(interval)


if __name__ == "__main__":
    main()
