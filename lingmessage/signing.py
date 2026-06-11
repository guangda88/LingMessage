"""灵信签名模块 — 为消息提供身份验证和防篡改保护

使用 HMAC-SHA256 对消息进行签名，确保：
1. 消息来源可信（签名需要私钥）
2. 消息内容未被篡改（签名绑定到内容）
"""

import hashlib
import hmac
import json

from lingmessage.types import Message, SourceType


def _get_message_content_hash(message: Message) -> str:
    """计算消息内容的哈希值，用于签名。

    仅包含关键业务字段，忽略 metadata 和 source_trace，
    使签名更稳定。
    """
    content = {
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "sender": message.sender.value,
        "recipient": message.recipient.value,
        "message_type": message.message_type.value,
        "channel": message.channel.value,
        "subject": message.subject,
        "body": message.body,
        "timestamp": message.timestamp,
        "reply_to": message.reply_to,
        "delivery_status": message.delivery_status.value,
    }
    if message.metadata:
        content["metadata"] = json.dumps(message.metadata, sort_keys=True, separators=(",", ":"))
    content_str = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content_str.encode("utf-8")).hexdigest()


def sign_message(message: Message, secret_key: str) -> str:
    """为消息生成签名。

    Args:
        message: 要签名的消息对象
        secret_key: 私钥（用于 HMAC）

    Returns:
        签名字符串（十六进制格式）

    Example:
        >>> msg = create_message(...)
        >>> signature = sign_message(msg, "my_secret_key")
        >>> # 存储 signature 到消息的 metadata 或独立字段
    """
    content_hash = _get_message_content_hash(message)
    hmac_obj = hmac.new(
        secret_key.encode("utf-8"),
        content_hash.encode("utf-8"),
        hashlib.sha256,
    )
    signature = hmac_obj.hexdigest()
    return signature


def verify_signature(
    message: Message,
    signature: str,
    secret_key: str,
) -> bool:
    """验证消息签名。

    Args:
        message: 要验证的消息对象
        signature: 待验证的签名
        secret_key: 私钥（必须与签名时使用的相同）

    Returns:
        True 如果签名有效，False 否则

    Example:
        >>> msg = load_message(...)
        >>> is_valid = verify_signature(msg, stored_signature, "my_secret_key")
        >>> if is_valid:
        ...     print("消息来源可信且未被篡改")
    """
    expected_signature = sign_message(message, secret_key)
    return hmac.compare_digest(expected_signature, signature)


def annotate_as_verified(message: Message, signature: str) -> Message:
    """将消息标记为已验证，并记录签名。

    这会创建一个新的 Message 对象，设置 source_type 为 VERIFIED，
    并将签名存储在 source_trace 中。

    Args:
        message: 原始消息
        signature: 已验证的签名

    Returns:
        新的 Message 对象（不可变，需要使用 dataclasses.replace）

    Example:
        >>> msg = create_message(...)
        >>> sig = sign_message(msg, "my_secret_key")
        >>> verified_msg = annotate_as_verified(msg, sig)
    """
    from dataclasses import replace

    # 注意：由于 Message 是 frozen=True，我们需要使用 replace 创建新对象
    return replace(
        message,
        source_type=SourceType.VERIFIED,
        source_trace=f"signature:{signature}",
    )
