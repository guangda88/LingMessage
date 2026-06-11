"""签名模块测试 - 验证 HMAC-SHA256 签名和验证功能"""

from pathlib import Path

from lingmessage.mailbox import Mailbox
from lingmessage.signing import (
    _get_message_content_hash,
    annotate_as_verified,
    sign_message,
    verify_signature,
)
from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    SourceType,
    create_message,
)


class TestGetMessageContentHash:
    """测试消息内容哈希计算"""

    def test_hash_consistency(self) -> None:
        """相同内容（包括 message_id）应生成相同哈希"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        hash1 = _get_message_content_hash(msg)
        hash2 = _get_message_content_hash(msg)
        assert hash1 == hash2

    def test_hash_includes_metadata(self) -> None:
        """哈希应包含 metadata（VULN-14 修复后）"""
        msg1 = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            metadata={"key1": "value1"},
        )
        from dataclasses import replace

        msg2 = replace(msg1, metadata=tuple([("key2", "value2")]))
        hash1 = _get_message_content_hash(msg1)
        hash2 = _get_message_content_hash(msg2)
        assert hash1 != hash2

    def test_hash_ignores_source_trace(self) -> None:
        """哈希应忽略 source_trace"""
        msg1 = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            source_trace="trace1",
        )
        from dataclasses import replace

        msg2 = replace(msg1, source_trace="trace2")
        hash1 = _get_message_content_hash(msg1)
        hash2 = _get_message_content_hash(msg2)
        assert hash1 == hash2

    def test_hash_different_content(self) -> None:
        """不同内容应生成不同哈希"""
        msg1 = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test1",
            body="body1",
        )
        msg2 = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test2",
            body="body2",
        )
        hash1 = _get_message_content_hash(msg1)
        hash2 = _get_message_content_hash(msg2)
        assert hash1 != hash2

    def test_hash_ignores_source_type(self) -> None:
        """哈希应忽略 source_type"""
        msg1 = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            source_type=SourceType.INFERRED,
        )
        from dataclasses import replace

        msg2 = replace(msg1, source_type=SourceType.GENERATED)
        hash1 = _get_message_content_hash(msg1)
        hash2 = _get_message_content_hash(msg2)
        assert hash1 == hash2


class TestSignVerifyRoundtrip:
    """测试签名和验证的往返流程"""

    def test_sign_verify_valid(self) -> None:
        """有效签名应通过验证"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        secret_key = "test_secret_key"
        signature = sign_message(msg, secret_key)
        is_valid = verify_signature(msg, signature, secret_key)
        assert is_valid is True

    def test_sign_verify_wrong_key(self) -> None:
        """错误密钥应导致验证失败"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        signature = sign_message(msg, "correct_key")
        is_valid = verify_signature(msg, signature, "wrong_key")
        assert is_valid is False

    def test_sign_verify_tampered_body(self) -> None:
        """篡改消息正文应导致验证失败"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="original body",
        )
        secret_key = "test_secret_key"
        signature = sign_message(msg, secret_key)

        # 篡改消息
        from dataclasses import replace

        tampered_msg = replace(msg, body="tampered body")
        is_valid = verify_signature(tampered_msg, signature, secret_key)
        assert is_valid is False

    def test_sign_verify_tampered_subject(self) -> None:
        """篡改消息主题应导致验证失败"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="original subject",
            body="test body",
        )
        secret_key = "test_secret_key"
        signature = sign_message(msg, secret_key)

        # 篡改消息
        from dataclasses import replace

        tampered_msg = replace(msg, subject="tampered subject")
        is_valid = verify_signature(tampered_msg, signature, secret_key)
        assert is_valid is False

    def test_sign_verify_metadata_tamper_detected(self) -> None:
        """篡改 metadata 应导致验证失败（VULN-14 修复后，metadata 参与签名）"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            metadata={"key": "value"},
        )
        secret_key = "test_secret_key"
        signature = sign_message(msg, secret_key)

        from dataclasses import replace

        tampered_msg = replace(msg, metadata=tuple([("new_key", "new_value")]))
        is_valid = verify_signature(tampered_msg, signature, secret_key)
        assert is_valid is False

    def test_sign_deterministic(self) -> None:
        """同一消息多次签名应产生相同结果"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        secret_key = "test_secret_key"
        sig1 = sign_message(msg, secret_key)
        sig2 = sign_message(msg, secret_key)
        sig3 = sign_message(msg, secret_key)
        assert sig1 == sig2 == sig3


class TestAnnotateAsVerified:
    """测试消息验证标注功能"""

    def test_annotate_sets_verified_type(self) -> None:
        """标注应设置 source_type 为 VERIFIED"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            source_type=SourceType.INFERRED,
        )
        signature = "test_signature"
        verified_msg = annotate_as_verified(msg, signature)
        assert verified_msg.source_type == SourceType.VERIFIED

    def test_annotate_stores_signature(self) -> None:
        """标注应将签名存储在 source_trace 中"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        signature = "test_signature_abc123"
        verified_msg = annotate_as_verified(msg, signature)
        assert verified_msg.source_trace == f"signature:{signature}"

    def test_annotate_preserves_other_fields(self) -> None:
        """标注应保留其他字段不变"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test subject",
            body="test body",
            metadata={"key": "value"},
            source_trace="original_trace",
        )
        signature = "test_signature"
        verified_msg = annotate_as_verified(msg, signature)
        assert verified_msg.sender == msg.sender
        assert verified_msg.recipient == msg.recipient
        assert verified_msg.message_type == msg.message_type
        assert verified_msg.channel == msg.channel
        assert verified_msg.subject == msg.subject
        assert verified_msg.body == msg.body
        assert verified_msg.metadata == msg.metadata

    def test_annotate_creates_new_object(self) -> None:
        """标注应创建新对象，不修改原消息"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
            source_type=SourceType.INFERRED,
        )
        signature = "test_signature"
        verified_msg = annotate_as_verified(msg, signature)
        # 原消息不应被修改
        assert msg.source_type == SourceType.INFERRED
        assert msg.source_trace == ""
        # 新消息应被正确标注
        assert verified_msg.source_type == SourceType.VERIFIED
        assert verified_msg.source_trace == f"signature:{signature}"


class TestPersistenceAndRecovery:
    """测试签名在消息持久化和恢复后的行为"""

    def test_signature_survives_serialization(self) -> None:
        """签名应在序列化/反序列化后仍然有效"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        secret_key = "test_secret_key"
        original_signature = sign_message(msg, secret_key)

        # 序列化
        msg_dict = msg.to_dict()

        # 反序列化
        recovered_msg = Message.from_dict(msg_dict)

        # 使用原始签名验证恢复的消息
        is_valid = verify_signature(recovered_msg, original_signature, secret_key)
        assert is_valid is True

    def test_signature_survives_mailbox_storage(self, tmp_path: Path) -> None:
        """签名应在通过 Mailbox 存储后仍然有效"""
        mailbox = Mailbox(root=tmp_path / "mailbox")
        header, msg = mailbox.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="test topic",
            subject="test subject",
            body="test body",
        )

        secret_key = "test_secret_key"
        original_signature = sign_message(msg, secret_key)

        # 从 Mailbox 重新加载消息
        loaded_messages = mailbox.load_thread_messages(header.thread_id)
        assert len(loaded_messages) == 1
        loaded_msg = loaded_messages[0]

        # 使用原始签名验证加载的消息
        is_valid = verify_signature(loaded_msg, original_signature, secret_key)
        assert is_valid is True


class TestSecurityProperties:
    """测试安全属性"""

    def test_different_keys_produce_different_signatures(self) -> None:
        """不同密钥应产生不同签名"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        sig1 = sign_message(msg, "key1")
        sig2 = sign_message(msg, "key2")
        assert sig1 != sig2

    def test_signature_length_is_fixed(self) -> None:
        """HMAC-SHA256 签名应有固定长度（64 十六进制字符）"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        signature = sign_message(msg, "test_key")
        # SHA-256 输出 32 字节，十六进制编码后为 64 字符
        assert len(signature) == 64
        # 验证是有效的十六进制字符串
        int(signature, 16)

    def test_empty_secret_key_allowed(self) -> None:
        """空密钥应被允许（虽然不推荐）"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="test body",
        )
        signature = sign_message(msg, "")
        is_valid = verify_signature(msg, signature, "")
        assert is_valid is True

    def test_unicode_secret_key(self) -> None:
        """Unicode 密钥应被正确处理"""
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="测试主题",
            body="测试正文 🧪",
        )
        secret_key = "密钥 🔑 测试"
        signature = sign_message(msg, secret_key)
        is_valid = verify_signature(msg, signature, secret_key)
        assert is_valid is True
