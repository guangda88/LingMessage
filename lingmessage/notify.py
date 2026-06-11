"""灵信通知 — 发信后 ding 收信人"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger("lingmessage.notify")

NOTIFY_TIMEOUT_SECONDS = 5
NOTIFY_MAX_RETRIES = 2
NOTIFY_BACKOFF_BASE = 0.5

DELIVERY_LOG = Path.home() / ".lingmessage" / "delivery_failures.log"


def ding_recipient(recipient: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget notification to a recipient's endpoint.

    Runs in a background thread to avoid blocking post().
    On failure: logs and moves on, never raises.
    """
    endpoint = _get_endpoint(recipient)
    if not endpoint:
        return

    def _send() -> None:
        for attempt in range(NOTIFY_MAX_RETRIES):
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = Request(endpoint, data=data, headers={"Content-Type": "application/json"})
                resp = urlopen(req, timeout=NOTIFY_TIMEOUT_SECONDS)
                if 200 <= resp.status < 300:
                    logger.info(f"Ding delivered to {recipient} (attempt {attempt + 1})")
                    return
                logger.warning(f"Ding to {recipient} returned status {resp.status}")
            except URLError as e:
                logger.debug(f"Ding to {recipient} failed (attempt {attempt + 1}): {e}")
            except Exception as e:
                logger.debug(f"Ding to {recipient} error: {e}")
                break
        _log_failure(recipient, endpoint, payload)

    t = threading.Thread(target=_send, daemon=True)
    t.start()


def _get_endpoint(participant: str) -> str | None:
    """Resolve participant identity to an MCP endpoint URL."""
    try:
        from lingmessage.types import IdentityRegistry, LingIdentity

        reg = IdentityRegistry.default()
        identity = LingIdentity(participant)
        entry = reg.get(identity)
        if entry and entry.mcp_server_key:
            return f"http://localhost:3000/mcp/{entry.mcp_server_key}"
    except (ValueError, ImportError):
        pass
    return None


def _log_failure(participant: str, endpoint: str, payload: dict[str, Any]) -> None:
    try:
        DELIVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "participant": participant,
            "endpoint": endpoint,
            "payload_keys": list(payload.keys()),
        }
        with DELIVERY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug(f"Failed to log delivery failure: {e}")
