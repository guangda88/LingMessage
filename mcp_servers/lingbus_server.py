"""灵信消息总线 MCP Server — SQLite WAL 消息总线服务"""

from pathlib import Path

from fastmcp import FastMCP

from lingmessage.lingbus import LingBus

mcp = FastMCP("lingmessage-lingbus")


def _get_bus(db_path: str) -> LingBus:
    return LingBus(Path(db_path).expanduser())


@mcp.tool()
def open_thread(
    db_path: str,
    topic: str,
    sender: str,
    recipients: str,
    channel: str = "ecosystem",
    subject: str = "",
    body: str = "",
) -> dict:
    """在消息总线中创建新线程。

    Args:
        db_path: SQLite 数据库路径
        topic: 议题
        sender: 发送者身份（如 lingflow）
        recipients: 接收者列表（逗号分隔）
        channel: 频道名
        subject: 主题
        body: 正文

    Returns:
        {"thread_id": str, "message_id": str}
    """
    bus = _get_bus(db_path)
    try:
        tid, mid = bus.open_thread(
            topic=topic,
            sender=sender,
            recipients=recipients.split(","),
            channel=channel,
            subject=subject,
            body=body,
        )
        return {"thread_id": tid, "message_id": mid}
    finally:
        bus.close()


@mcp.tool()
def post_reply(
    db_path: str,
    thread_id: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
) -> dict:
    """在消息总线中回复线程。

    Returns:
        {"message_id": str}
    """
    bus = _get_bus(db_path)
    try:
        mid = bus.post_reply(
            thread_id=thread_id,
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
        )
        return {"message_id": mid}
    finally:
        bus.close()


@mcp.tool()
def poll_messages(
    db_path: str,
    recipient: str,
    since_rowid: int = 0,
    limit: int = 100,
) -> list[dict]:
    """轮询接收者的新消息。

    Returns:
        消息列表
    """
    bus = _get_bus(db_path)
    try:
        msgs = bus.poll(recipient=recipient, since_rowid=since_rowid, limit=limit)
        return [
            {
                "rowid": m.rowid,
                "thread_id": m.thread_id,
                "message_id": m.message_id,
                "sender": m.sender,
                "recipient": m.recipient,
                "subject": m.subject,
                "body": m.body,
                "timestamp": m.timestamp,
            }
            for m in msgs
        ]
    finally:
        bus.close()


@mcp.tool()
def ack_message(db_path: str, message_id: str, member: str) -> dict:
    """确认消息已读。

    Args:
        db_path: SQLite 数据库路径
        message_id: 消息 ID
        member: 确认成员身份

    Returns:
        {"success": bool}
    """
    bus = _get_bus(db_path)
    try:
        bus.ack(message_id=message_id, member=member)
        return {"success": True}
    finally:
        bus.close()


@mcp.tool()
def get_stats(db_path: str) -> dict:
    """获取消息总线统计信息。

    Returns:
        线程数、消息数、未确认数等
    """
    bus = _get_bus(db_path)
    try:
        return bus.stats()
    finally:
        bus.close()


if __name__ == "__main__":
    mcp.run()
