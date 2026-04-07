"""灵信签名 MCP Server — 消息签名与验证服务"""

from fastmcp import FastMCP

from lingmessage.signing import annotate_as_verified, sign_message, verify_signature
from lingmessage.types import Channel, LingIdentity, MessageType, SourceType, create_message

mcp = FastMCP("lingmessage-signing")


def _dict_to_message(data: dict) -> "Message":
    """将字典转换为 Message 对象。"""
    from lingmessage.types import Message

    if isinstance(data.get("source_type"), str):
        data["source_type"] = SourceType(data["source_type"])
    if "message_id" in data and "thread_id" in data:
        return Message.from_dict(data)
    return create_message(
        sender=LingIdentity(data["sender"]),
        recipient=LingIdentity(data.get("recipient", "lingyi")),
        message_type=MessageType(data.get("message_type", "open")),
        channel=Channel(data.get("channel", "ecosystem")),
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        thread_id=data.get("thread_id", ""),
    )


@mcp.tool()
def sign(msg: dict, secret_key: str) -> str:
    """为灵信消息生成 HMAC-SHA256 签名。

    Args:
        msg: 消息字典（需含 sender, body 等字段）
        secret_key: 签名密钥

    Returns:
        十六进制签名字符串（64字符）
    """
    message = _dict_to_message(msg)
    return sign_message(message, secret_key)


@mcp.tool()
def verify(msg: dict, signature: str, secret_key: str) -> dict:
    """验证灵信消息签名是否有效。

    Args:
        msg: 消息字典
        signature: 待验证的签名
        secret_key: 签名密钥

    Returns:
        {"valid": bool, "source_type": str}
    """
    message = _dict_to_message(msg)
    valid = verify_signature(message, signature, secret_key)
    return {"valid": valid, "source_type": message.source_type.value}


@mcp.tool()
def annotate_verified(msg: dict, signature: str) -> dict:
    """将消息标记为已验证，返回带 VERIFIED 标记的消息字典。

    Args:
        msg: 消息字典
        signature: 已验证的签名

    Returns:
        标记后的消息字典（source_type=verified）
    """
    message = _dict_to_message(msg)
    verified = annotate_as_verified(message, signature)
    return verified.to_dict()


if __name__ == "__main__":
    mcp.run()
