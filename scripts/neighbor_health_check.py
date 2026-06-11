#!/usr/bin/env python3
"""SDT-2 升级版：邻居HTTP健康检查

从端口监听检查升级到HTTP端点探测。
端口监听(ss -tlnp)只能确认进程在监听，不能确认服务正常响应。
HTTP探测能验证：端口可达 + 服务响应 + 响应时间。

用法：
    python scripts/neighbor_health_check.py           # 单次检查
    python scripts/neighbor_health_check.py --json     # JSON输出（供dashboard）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class HealthResult:
    name: str
    port: int
    endpoint: str
    http_status: int | None
    reachable: bool
    latency_ms: float | None
    error: str | None


NEIGHBORS = [
    ("灵网", 8300, "/api/stats"),
    ("智桥", 8765, "/"),
    ("灵犀", 9529, "/"),
    ("灵通模型", 8100, "/"),
]

TIMEOUT = 5.0


def check_one(name: str, port: int, path: str) -> HealthResult:
    url = f"http://localhost:{port}{path}"
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            latency = (time.monotonic() - t0) * 1000
            return HealthResult(
                name=name, port=port, endpoint=path,
                http_status=r.status, reachable=True,
                latency_ms=round(latency, 1), error=None,
            )
    except urllib.error.HTTPError as e:
        latency = (time.monotonic() - t0) * 1000
        return HealthResult(
            name=name, port=port, endpoint=path,
            http_status=e.code, reachable=True,
            latency_ms=round(latency, 1), error=str(e),
        )
    except Exception as e:
        return HealthResult(
            name=name, port=port, endpoint=path,
            http_status=None, reachable=False,
            latency_ms=None, error=f"{type(e).__name__}: {e}",
        )


def run_check() -> list[HealthResult]:
    return [check_one(n, p, path) for n, p, path in NEIGHBORS]


def print_human(results: list[HealthResult]) -> int:
    print(f"=== SDT-2 邻居HTTP健康检查 {datetime.now(timezone.utc).isoformat()} ===")
    all_ok = True
    for r in results:
        status_icon = "✅" if r.reachable else "❌"
        latency_str = f"{r.latency_ms}ms" if r.latency_ms is not None else "—"
        print(f"  {status_icon} {r.name} :{r.port}{r.endpoint}  "
              f"HTTP {r.http_status or 'N/A'}  {latency_str}")
        if r.error and r.reachable:
            print(f"      error: {r.error}")
        if not r.reachable:
            all_ok = False
    print()
    up = sum(1 for r in results if r.reachable)
    print(f"总计: {up}/{len(results)} 可达")
    return 0 if all_ok else 1


def print_json(results: list[HealthResult]) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "reachable": sum(1 for r in results if r.reachable),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="邻居HTTP健康检查")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    results = run_check()
    if args.json:
        print_json(results)
        return 0
    return print_human(results)


if __name__ == "__main__":
    sys.exit(main())
