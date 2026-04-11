"""灵信能力注册表 — MCP server 动态注册/注销协议

Phase 1: 能力注册协议
- MCP server 启动时注册工具清单
- 注销时清理注册
- 注册信息持久化到 ~/.lingmessage/capability_registry.json
- LingFlow+ 启动时查询注册表，动态构建路由表
- 保留静态规则作为 fallback
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path.home() / ".lingmessage" / "capability_registry.json"

_ALLOWED_COMMANDS = frozenset({
    "python3", "python", "node", "npx", "npm",
    "uvicorn", "gunicorn", "hypercorn",
})

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_SAFE_TOOL_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


@dataclass(frozen=True)
class ServerCapability:
    server_key: str
    agent_id: str
    display_name: str
    tools: tuple[str, ...] = ()
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    working_dir: str = ""
    registered_at: str = ""
    last_heartbeat: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "server_key": self.server_key,
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "tools": list(self.tools),
            "transport": self.transport,
        }
        if self.command:
            d["command"] = self.command
        if self.args:
            d["args"] = list(self.args)
        if self.url:
            d["url"] = self.url
        if self.working_dir:
            d["working_dir"] = self.working_dir
        if self.registered_at:
            d["registered_at"] = self.registered_at
        if self.last_heartbeat:
            d["last_heartbeat"] = self.last_heartbeat
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerCapability:
        command = data.get("command", "")
        if command and os.path.basename(command) not in _ALLOWED_COMMANDS:
            raise ValueError(f"Command not in allowlist: {command!r}")
        url = data.get("url", "")
        if url:
            _validate_localhost_url(url)
        working_dir = data.get("working_dir", "")
        if working_dir and ".." in Path(working_dir).parts:
            raise ValueError(f"working_dir must not contain '..': {working_dir!r}")
        tools_raw = data.get("tools", [])
        for t in tools_raw:
            if not _SAFE_TOOL_RE.fullmatch(t):
                raise ValueError(f"Invalid tool name: {t!r}")
        server_key = data["server_key"]
        if not _SAFE_NAME_RE.fullmatch(server_key):
            raise ValueError(f"Invalid server_key: {server_key!r}")
        return cls(
            server_key=server_key,
            agent_id=data["agent_id"],
            display_name=data.get("display_name", data.get("name", "")),
            tools=tuple(tools_raw),
            transport=data.get("transport", "stdio"),
            command=command,
            args=tuple(data.get("args", [])),
            url=url,
            working_dir=working_dir,
            registered_at=data.get("registered_at", ""),
            last_heartbeat=data.get("last_heartbeat", ""),
        )


@dataclass
class CapabilityRegistry:
    _path: Path = field(default_factory=lambda: REGISTRY_PATH)
    _servers: dict[str, ServerCapability] = field(default_factory=dict)

    def register(self, capability: ServerCapability) -> None:
        if not _SAFE_NAME_RE.fullmatch(capability.server_key):
            raise ValueError(f"Invalid server_key: {capability.server_key!r}")
        now = _now_epoch_iso()
        if not capability.registered_at:
            cap = replace(capability, registered_at=now, last_heartbeat=now)
        else:
            cap = replace(capability, last_heartbeat=now)
        self._servers[cap.server_key] = cap
        self._save()
        logger.info(f"Registered server: {cap.server_key} ({len(cap.tools)} tools)")

    def unregister(self, server_key: str) -> bool:
        if server_key in self._servers:
            del self._servers[server_key]
            self._save()
            logger.info(f"Unregistered server: {server_key}")
            return True
        return False

    def heartbeat(self, server_key: str) -> bool:
        cap = self._servers.get(server_key)
        if cap:
            self._servers[server_key] = replace(cap, last_heartbeat=_now_epoch_iso())
            self._save()
            return True
        return False

    def get(self, server_key: str) -> ServerCapability | None:
        return self._servers.get(server_key)

    def find_tool(self, tool_name: str) -> list[ServerCapability]:
        return [c for c in self._servers.values() if tool_name in c.tools]

    def find_tool_best(self, tool_name: str) -> ServerCapability | None:
        providers = self.find_tool(tool_name)
        if not providers:
            return None
        active = [p for p in providers if _is_active(p)]
        if active:
            return active[0]
        return providers[0]

    def list_servers(self) -> list[ServerCapability]:
        return list(self._servers.values())

    def list_active(self, max_age_seconds: int = 600) -> list[ServerCapability]:
        return [c for c in self._servers.values() if _is_active(c, max_age_seconds)]

    def get_all_tools(self) -> dict[str, list[str]]:
        tools: dict[str, list[str]] = {}
        for cap in self._servers.values():
            aid = cap.agent_id
            if aid not in tools:
                tools[aid] = []
            tools[aid].extend(cap.tools)
        for k in tools:
            tools[k] = list(dict.fromkeys(tools[k]))
        return tools

    def get_routing_table(self) -> dict[str, str]:
        table: dict[str, str] = {}
        for cap in self._servers.values():
            for tool in cap.tools:
                if tool not in table:
                    table[tool] = cap.server_key
        return table

    def stats(self) -> dict[str, Any]:
        total_tools = sum(len(c.tools) for c in self._servers.values())
        active = sum(1 for c in self._servers.values() if _is_active(c))
        return {
            "total_servers": len(self._servers),
            "active_servers": active,
            "total_tools": total_tools,
            "unique_tools": len(self.get_routing_table()),
        }

    def merge_from_mcp_registry(self, mcp_servers: dict[str, Any]) -> int:
        merged = 0
        for key, config in mcp_servers.items():
            if not _SAFE_NAME_RE.fullmatch(key):
                logger.warning(f"Skipping invalid server_key in merge: {key!r}")
                continue
            if key in self._servers:
                continue
            if hasattr(config, "__dataclass_fields__"):
                tools = getattr(config, "tools", [])
                args = getattr(config, "args", [])
                command = getattr(config, "command", "")
                working_dir = getattr(config, "working_dir", "")
                agent_id = getattr(config, "agent_id", key)
                display_name = getattr(config, "name", key)
            elif isinstance(config, dict):
                tools = config.get("tools", [])
                args = config.get("args", [])
                command = config.get("command", "")
                working_dir = config.get("working_dir", "")
                agent_id = config.get("agent_id", key)
                display_name = config.get("name", key)
            else:
                continue

            try:
                cap = ServerCapability(
                    server_key=key,
                    agent_id=agent_id,
                    display_name=display_name,
                    tools=tuple(tools),
                    transport="stdio",
                    command=command or "",
                    args=tuple(args),
                    working_dir=str(working_dir) if working_dir else "",
                )
                ServerCapability.from_dict(cap.to_dict())
            except ValueError as e:
                logger.warning(f"Skipping invalid MCP server {key}: {e}")
                continue
            self._servers[key] = cap
            merged += 1
        if merged:
            self._save()
        return merged

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "updated_at": _now_epoch_iso(),
            "servers": {k: v.to_dict() for k, v in self._servers.items()},
        }
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
            os.chmod(self._path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: Path | None = None) -> CapabilityRegistry:
        p = path or REGISTRY_PATH
        reg = cls(_path=p)
        if not p.exists():
            return reg
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for key, sdata in data.get("servers", {}).items():
                reg._servers[key] = ServerCapability.from_dict(sdata)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load capability registry: {e}")
        return reg

    @classmethod
    def default(cls) -> CapabilityRegistry:
        return cls.load()


def _now_epoch_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _validate_localhost_url(url: str) -> None:
    """Reject non-localhost URLs to prevent SSRF."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Invalid URL: {url!r}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs allowed: {url!r}")
    if parsed.hostname not in _LOCALHOST_HOSTS:
        raise ValueError(f"Only localhost URLs allowed: {url!r}")


def _is_active(cap: ServerCapability, max_age_seconds: int = 600) -> bool:
    if not cap.last_heartbeat:
        return False
    try:
        from datetime import datetime, timezone
        hb = datetime.fromisoformat(cap.last_heartbeat)
        age = (datetime.now(timezone.utc) - hb).total_seconds()
        return age < max_age_seconds
    except (ValueError, TypeError):
        return False
