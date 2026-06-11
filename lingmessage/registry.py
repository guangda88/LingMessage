"""
灵字辈工具注册表 (Ling Family Tool Registry)

用法：
    from lingmessage.registry import ToolRegistry

    reg = ToolRegistry()

    # 注册（MCP server 启动时调用）
    reg.register("lingclaude", "灵克", [
        {"name": "read_file", "category": "filesystem", "description": "读取文件"},
        {"name": "write_file", "category": "filesystem", "description": "写入文件"},
    ])

    # 查询
    tools = reg.list_tools()           # 全部工具
    tools = reg.list_tools("lingclaude")  # 灵克的工具
    tool = reg.find_tool("read_file")  # 按名查找

    # 统计
    stats = reg.stats()
    # {"servers": 8, "tools": 152, "by_category": {...}}

注册表存储: ~/.lingmessage/tool_registry.json
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


REGISTRY_PATH = Path(os.environ.get(
    "LING_TOOL_REGISTRY",
    str(Path.home() / ".lingmessage" / "tool_registry.json"),
))


class ToolRegistry:
    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else REGISTRY_PATH
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "version": "1.0.0",
            "created_at": time.strftime("%Y-%m-%d"),
            "updated_at": time.strftime("%Y-%m-%d"),
            "description": "灵字辈工具注册表",
            "servers": {},
        }

    def _save(self) -> None:
        self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def register(
        self,
        server_id: str,
        server_name: str,
        tools: List[Dict[str, Any]],
        server_role: str = "",
        transport: str = "stdio",
    ) -> int:
        """注册一个 MCP server 的工具列表。返回注册的工具数。

        每次调用覆盖该 server 的旧注册。
        """
        server_entry = {
            "name": server_name,
            "role": server_role,
            "transport": transport,
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool_count": len(tools),
            "tools": tools,
        }
        self._data["servers"][server_id] = server_entry
        self._save()
        return len(tools)

    def unregister(self, server_id: str) -> bool:
        """移除一个 server 的注册。"""
        if server_id in self._data["servers"]:
            del self._data["servers"][server_id]
            self._save()
            return True
        return False

    def list_servers(self) -> List[Dict[str, Any]]:
        """列出所有已注册的 server。"""
        result = []
        for sid, info in self._data["servers"].items():
            result.append({
                "id": sid,
                "name": info["name"],
                "role": info.get("role", ""),
                "tool_count": info["tool_count"],
                "registered_at": info.get("registered_at", ""),
            })
        return result

    def list_tools(self, server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出工具。可按 server 过滤。每个工具带 server_id 和 server_name。"""
        tools = []
        servers = self._data.get("servers", {})
        for sid, info in servers.items():
            if server_id and sid != server_id:
                continue
            for tool in info.get("tools", []):
                tools.append({
                    **tool,
                    "server_id": sid,
                    "server_name": info["name"],
                })
        return tools

    def find_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """按工具名查找（精确匹配）。"""
        for tool in self.list_tools():
            if tool["name"] == name:
                return tool
        return None

    def find_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        """按分类查找工具。"""
        return [
            t for t in self.list_tools()
            if t.get("category", "").lower() == category.lower()
        ]

    def stats(self) -> Dict[str, Any]:
        """统计信息：server 数、工具总数、按分类统计。"""
        tools = self.list_tools()
        by_category: Dict[str, int] = {}
        by_server: Dict[str, int] = {}
        for t in tools:
            cat = t.get("category", "uncategorized")
            by_category[cat] = by_category.get(cat, 0) + 1
            sid = t["server_id"]
            by_server[sid] = by_server.get(sid, 0) + 1

        return {
            "servers": len(self._data["servers"]),
            "tools": len(tools),
            "unique_tools": len({t["name"] for t in tools}),
            "by_category": by_category,
            "by_server": by_server,
        }

    def route(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """根据工具名路由到对应的 server。返回 server 信息 + tool 信息。"""
        tool = self.find_tool(tool_name)
        if not tool:
            return None
        sid = tool["server_id"]
        server = self._data["servers"].get(sid, {})
        return {
            "server_id": sid,
            "server_name": server.get("name", ""),
            "transport": server.get("transport", "stdio"),
            "tool": tool,
        }


def register_fastmcp_server(
    server_id: str,
    server_name: str,
    mcp_instance,
    server_role: str = "",
    transport: str = "stdio",
) -> int:
    """从 FastMCP 实例自动提取工具并注册到 registry。

    在 MCP server 的 main() 函数中、mcp.run() 之前调用。

    用法:
        from lingmessage.registry import register_fastmcp_server
        register_fastmcp_server("lingclaude", "灵克", mcp)
        mcp.run()

    Args:
        server_id: 唯一标识符，如 "lingclaude"
        server_name: 显示名称，如 "灵克"
        mcp_instance: FastMCP 实例
        server_role: 角色描述
        transport: 传输类型

    Returns:
        注册的工具数量
    """
    tools = []

    # FastMCP stores tools in _tool_manager._tools (dict of name -> Tool)
    tool_manager = getattr(mcp_instance, "_tool_manager", None)
    if tool_manager:
        tools_dict = getattr(tool_manager, "_tools", {})
        for name, tool_obj in tools_dict.items():
            desc = getattr(tool_obj, "description", "") or ""
            tools.append({
                "name": name,
                "category": "auto",
                "description": desc[:200] if desc else "",
            })

    # Fallback: try _tools attribute directly
    if not tools:
        raw_tools = getattr(mcp_instance, "_tools", {})
        if isinstance(raw_tools, dict):
            for name, tool_obj in raw_tools.items():
                desc = getattr(tool_obj, "description", "") or ""
                tools.append({
                    "name": name,
                    "category": "auto",
                    "description": desc[:200] if desc else "",
                })

    reg = ToolRegistry()
    count = reg.register(server_id, server_name, tools, server_role, transport)

    import logging
    logging.getLogger(__name__).info(
        f"Registered {count} tools from {server_name} ({server_id}) to tool registry"
    )
    return count


if __name__ == "__main__":
    import sys

    reg = ToolRegistry()

    if len(sys.argv) < 2:
        stats = reg.stats()
        print("灵字辈工具注册表")
        print(f"  MCP 服务器: {stats['servers']}")
        print(f"  注册工具: {stats['tools']}")
        print(f"  去重工具: {stats['unique_tools']}")
        print()
        for s in reg.list_servers():
            print(f"  {s['id']:20s} {s['name']:6s} {s['tool_count']:3d} 工具  ({s.get('role', '')})")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "tools":
        server_id = sys.argv[2] if len(sys.argv) > 2 else None
        for t in reg.list_tools(server_id):
            print(f"  {t['server_name']:6s} | {t['name']:30s} | {t.get('category', '?'):15s} | {t.get('description', '')}")

    elif cmd == "find":
        if len(sys.argv) < 3:
            print("Usage: registry.py find <tool_name>")
            sys.exit(1)
        result = reg.find_tool(sys.argv[2])
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"工具 '{sys.argv[2]}' 未找到")

    elif cmd == "route":
        if len(sys.argv) < 3:
            print("Usage: registry.py route <tool_name>")
            sys.exit(1)
        result = reg.route(sys.argv[2])
        if result:
            print(f"→ {result['server_name']} ({result['server_id']}) via {result['transport']}")
            print(f"  tool: {result['tool']['name']} — {result['tool'].get('description', '')}")
        else:
            print(f"工具 '{sys.argv[2]}' 无路由")

    elif cmd == "stats":
        stats = reg.stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: tools [server_id], find <name>, route <name>, stats")
