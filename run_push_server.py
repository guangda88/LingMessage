"""LingBus Push SSE Server — 端口 9527

SSE (Server-Sent Events) 推送服务，写入即送达。

端点：
  GET /events?recipient=lingflow   — SSE 订阅（按收件人过滤）
  GET /events                      — SSE 订阅（接收所有消息）
  GET /health                      — 健康检查
  GET /stats                       — 订阅统计

启动：python3 run_push_server.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from lingmessage.push_manager import PushManager, Subscriber, get_push_manager

logger = logging.getLogger("lingbus-push")

BUS_DIR = Path.home() / ".lingmessage"

_SUB_EVENTS: dict[str, asyncio.Event] = {}
_SUB_LOCK = asyncio.Lock()


async def _sse_generator(sub: Subscriber, manager: PushManager):
    """为每个SSE连接生成事件流。"""
    connect_event = json.dumps({
        "type": "connected",
        "recipient": sub.recipient,
        "subscriber_count": manager.subscriber_count,
    }, ensure_ascii=False)
    yield f"event: connected\ndata: {connect_event}\n\n"

    disconnect_event = asyncio.Event()

    try:
        while not disconnect_event.is_set():
            try:
                data = await asyncio.wait_for(sub.queue.get(), timeout=30)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        manager.unsubscribe(sub)
        logger.info("SSE disconnected: recipient=%s remaining=%d", sub.recipient, manager.subscriber_count)


async def events(request: Request) -> Response:
    recipient = request.query_params.get("recipient", "all")
    manager = get_push_manager()
    sub = manager.subscribe(recipient)

    logger.info("SSE connected: recipient=%s subscribers=%d", recipient, manager.subscriber_count)

    return StreamingResponse(
        _sse_generator(sub, manager),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def health(request: Request) -> Response:
    manager = get_push_manager()
    return Response(
        json.dumps({"status": "ok", "subscribers": manager.subscriber_count}),
        media_type="application/json",
    )


async def stats(request: Request) -> Response:
    manager = get_push_manager()
    return Response(
        json.dumps(manager.get_stats(), ensure_ascii=False),
        media_type="application/json",
    )


async def internal_broadcast(request: Request) -> Response:
    """LingBus写入后调用此端点，将消息推送到SSE订阅者。"""
    manager = get_push_manager()
    body = await request.body()
    try:
        msg = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return Response(json.dumps({"error": "invalid json"}), status_code=400, media_type="application/json")

    delivered = manager.broadcast_all(msg)
    return Response(
        json.dumps({"delivered": delivered}, ensure_ascii=False),
        media_type="application/json",
    )


app = Starlette(
    routes=[
        Route("/events", events),
        Route("/health", health),
        Route("/stats", stats),
        Route("/internal/broadcast", internal_broadcast, methods=["POST"]),
    ],
)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    loop = asyncio.get_event_loop()
    manager = get_push_manager()
    manager.set_loop(loop)

    logger.info("Starting LingBus Push SSE Server on 127.0.0.1:9527")

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=9527,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        logger.info("Push server stopped. Final subscribers: %d", manager.subscriber_count)


if __name__ == "__main__":
    asyncio.run(main())
