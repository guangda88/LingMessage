#!/usr/bin/env python3
"""FastMCP HTTP Proxy 通用模板

将任意 fastmcp MCP Server 从 stdio 模式转换为 HTTP 模式。
每个 HTTP 请求创建独立的 transport+server 实例（无状态模式），
解决多 agent 并发场景下的进程爆炸问题。

使用方法：
1. 复制此文件到目标项目，如 run_<name>_http.py
2. 修改 SERVER_MODULE, SERVER_VAR, PORT 三个变量
3. 启动：python3 run_<name>_http.py
4. 进程管理：配合 run_<name>_http.sh 使用

参考：
- 灵信 lingbus 实现：/home/ai/lingmessage/run_lingbus_http.py
- 灵研 TS 参考：/home/ai/.zai/proxy/server.mjs
"""

import asyncio
import logging
import sys

# ============ 配置区（按项目修改） ============

# MCP server 的 import 路径。示例：
#   from mcp_servers.lingbus_server import mcp
#   from lingzhi.mcp_server import mcp
#   from lingresearch.mcp_server import server
SERVER_MODULE = "mcp_servers.lingbus_server"
SERVER_VAR = "mcp"
PORT = 9528
HOST = "127.0.0.1"
MCP_PATH = "/mcp"

# ============ 以下无需修改 ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-http-proxy")


def _import_server():
    """动态导入 MCP server 实例，避免硬编码 import。"""
    import importlib
    module = importlib.import_module(SERVER_MODULE)
    return getattr(module, SERVER_VAR)


async def main():
    mcp = _import_server()
    logger.info(
        "Starting %s HTTP Proxy on %s:%d%s",
        SERVER_MODULE, HOST, PORT, MCP_PATH,
    )
    try:
        await mcp.run_http_async(
            host=HOST,
            port=PORT,
            stateless_http=True,
            transport="http",
            path=MCP_PATH,
            show_banner=True,
        )
    except OSError as e:
        if "Address already in use" in str(e) or getattr(e, 'errno', 0) == 98:
            logger.critical("Port %d already in use. Aborting.", PORT)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
