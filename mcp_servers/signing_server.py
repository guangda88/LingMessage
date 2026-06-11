"""灵信签名 MCP Server — 消息签名与验证服务"""

from __future__ import annotations

import os

from fastmcp import FastMCP

from lingmessage.signing import annotate_as_verified, sign_message, verify_signature
from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    SourceType,
    create_message,
)

mcp = FastMCP("lingmessage-signing")


def _get_secret_key() -> str:
    """从环境变量获取签名密钥。lingmessage 签名需要 LINGMESSAGE_SIGNING_KEY 环境变量。"""
    key = os.environ.get("LINGMESSAGE_SIGNING_KEY", "")
    if not key:
        raise ValueError(
            "LINGMESSAGE_SIGNING_KEY 环境变量未设置。"
            "签名操作需要此密钥。请在 ~/.ling_keys.env 或环境中设置。"
        )
    return key


def _dict_to_message(data: dict) -> Message:
    """将字典转换为 Message 对象。"""

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
def sign(msg: dict) -> str:
    """为灵信消息生成 HMAC-SHA256 签名。签名密钥从环境变量 LINGMESSAGE_SIGNING_KEY 读取。

    Args:
        msg: 消息字典（需含 sender, body 等字段）

    Returns:
        十六进制签名字符串（64字符）
    """
    secret_key = _get_secret_key()
    message = _dict_to_message(msg)
    return sign_message(message, secret_key)


@mcp.tool()
def verify(msg: dict, signature: str) -> dict:
    """验证灵信消息签名是否有效。签名密钥从环境变量 LINGMESSAGE_SIGNING_KEY 读取。

    Args:
        msg: 消息字典
        signature: 待验证的签名

    Returns:
        {"valid": bool, "source_type": str}
    """
    secret_key = _get_secret_key()
    message = _dict_to_message(msg)
    valid = verify_signature(message, signature, secret_key)
    return {"valid": valid, "source_type": message.source_type.value}


@mcp.tool()
def annotate_verified(msg: dict, signature: str) -> dict:
    """将消息标记为已验证。必须通过签名验证才能标记。签名密钥从环境变量读取。

    Args:
        msg: 消息字典
        signature: 待验证的签名

    Returns:
        标记后的消息字典（source_type=verified）

    Raises:
        ValueError: 签名验证失败时拒绝标记
    """
    secret_key = _get_secret_key()
    message = _dict_to_message(msg)
    if not verify_signature(message, signature, secret_key):
        raise ValueError(
            "签名验证失败：无法将消息标记为 verified。"
            "请确保签名与消息内容匹配。"
        )
    verified = annotate_as_verified(message, signature)
    return verified.to_dict()


if __name__ == "__main__":
    try:
        from lingmessage.registry import register_fastmcp_server
        register_fastmcp_server("lingmessage-signing", "灵信·签名", mcp, "数字签名")
    except Exception:
        pass
    mcp.run()
