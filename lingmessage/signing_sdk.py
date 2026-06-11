"""灵信签名SDK — 供外部系统（智桥gateway、灵扬外联等）使用的轻量接口。

与 lingmessage.signing 的区别：
- signing.py 面向内部 Message 对象（需要完整dataclass）
- signing_sdk.py 面向外部字符串接口（sender + body 即可）

使用方式：
    from lingmessage.signing_sdk import sign_payload, verify_payload

    # 签名
    sig = sign_payload("zhibridge", '{"action":"approve","id":123}', key)

    # 验证
    ok = verify_payload("zhibridge", '{"action":"approve","id":123}', sig, key)
"""

from __future__ import annotations

import hashlib
import hmac
import os

DEFAULT_KEY_ENV = "LINGMESSAGE_SIGNING_KEY"


def _get_key(secret_key: str | None = None) -> str:
    """获取签名密钥，优先参数，其次环境变量。"""
    key = secret_key or os.environ.get(DEFAULT_KEY_ENV, "")
    if not key:
        raise ValueError(
            f"签名密钥未设置：请设置 {DEFAULT_KEY_ENV} 环境变量或传入 secret_key 参数"
        )
    return key


def sign_payload(
    sender: str,
    payload: str,
    secret_key: str | None = None,
) -> str:
    """对任意字符串负载生成HMAC-SHA256签名。

    Args:
        sender: 签名者身份（如 "zhibridge"）
        payload: 要签名的内容（JSON字符串、请求体等）
        secret_key: 密钥（默认从 LINGMESSAGE_SIGNING_KEY 环境变量读取）

    Returns:
        十六进制签名字符串

    Example:
        >>> sig = sign_payload("zhibridge", '{"id":123}')
        "a1b2c3d4..."
    """
    key = _get_key(secret_key)
    content = f"{sender}:{payload}"
    return hmac.new(
        key.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_payload(
    sender: str,
    payload: str,
    signature: str,
    secret_key: str | None = None,
) -> bool:
    """验证字符串负载的签名。

    Args:
        sender: 签名者身份
        payload: 原始内容
        signature: 待验证的签名
        secret_key: 密钥（默认从环境变量读取）

    Returns:
        True 如果签名有效

    Example:
        >>> verify_payload("zhibridge", '{"id":123}', "a1b2c3...", key)
        True
    """
    key = _get_key(secret_key)
    expected = sign_payload(sender, payload, key)
    return hmac.compare_digest(expected, signature)


def sign_request(
    sender: str,
    method: str,
    path: str,
    body: str,
    secret_key: str | None = None,
) -> str:
    """对HTTP请求签名（智桥gateway集成用）。

    签名覆盖 method + path + body，防止请求被篡改。

    Args:
        sender: 请求方身份
        method: HTTP方法（GET/POST/PUT/DELETE）
        path: 请求路径（如 /api/decisions/outreach-email）
        body: 请求体（GET请求传空字符串）
        secret_key: 密钥

    Returns:
        签名字符串
    """
    payload = f"{method.upper()}:{path}:{body}"
    return sign_payload(sender, payload, secret_key)


def verify_request(
    sender: str,
    method: str,
    path: str,
    body: str,
    signature: str,
    secret_key: str | None = None,
) -> bool:
    """验证HTTP请求签名。"""
    payload = f"{method.upper()}:{path}:{body}"
    return verify_payload(sender, payload, signature, secret_key)
