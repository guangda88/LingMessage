"""LingBus MCP HTTP Proxy — 端口 9528

将 LingBus MCP Server 从 stdio 模式转换为 HTTP 模式，
解决多 agent 并发场景下的进程爆炸问题。

启动：python3 run_lingbus_http.py
端点：http://127.0.0.1:9528/mcp
"""

import asyncio
import logging
import sys

from mcp_servers.lingbus_server import mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lingbus-http-proxy")

HOST = "127.0.0.1"
PORT = 9528


async def main():
    logger.info("Starting LingBus MCP HTTP Proxy on %s:%d/mcp", HOST, PORT)
    try:
        await mcp.run_http_async(
            host=HOST,
            port=PORT,
            stateless_http=True,
            transport="http",
            path="/mcp",
            show_banner=True,
        )
    except OSError as e:
        if "Address already in use" in str(e) or e.errno == 98:
            logger.critical("Port %d already in use. Aborting.", PORT)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
