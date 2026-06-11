from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lingmessage.auto_reply import (
    AdvisorVerdict,
    _advisor_review,
    _call_lingai_advisor,
    _is_generic_reply,
    _parse_verdict,
)


class TestParseVerdict:
    def test_good_rating(self) -> None:
        raw = '{"rating": "good", "reason": "有实质内容", "retry_hint": ""}'
        v = _parse_verdict(raw)
        assert v.rating == "good"
        assert v.should_suppress is False
        assert v.retry_suggested is False
        assert v.source == "lingai"

    def test_bad_rating(self) -> None:
        raw = '{"rating": "bad", "reason": "泛泛之词", "retry_hint": "补充具体数据"}'
        v = _parse_verdict(raw)
        assert v.rating == "bad"
        assert v.should_suppress is True
        assert v.retry_suggested is True
        assert v.retry_hint == "补充具体数据"

    def test_marginal_with_hint(self) -> None:
        raw = '{"rating": "marginal", "reason": "可发可不发", "retry_hint": "加深分析"}'
        v = _parse_verdict(raw)
        assert v.rating == "marginal"
        assert v.should_suppress is False
        assert v.retry_suggested is True

    def test_marginal_no_hint(self) -> None:
        raw = '{"rating": "marginal", "reason": "还行", "retry_hint": ""}'
        v = _parse_verdict(raw)
        assert v.retry_suggested is False

    def test_json_embedded_in_text(self) -> None:
        raw = '根据评估 {"rating": "good", "reason": "不错", "retry_hint": ""} 发布'
        v = _parse_verdict(raw)
        assert v.rating == "good"

    def test_unparseable_non_generic(self) -> None:
        raw = "this is not json but has real content"
        v = _parse_verdict(raw)
        assert v.rating == "good"
        assert v.should_suppress is False
        assert v.source == "lingai"

    def test_unparseable_generic(self) -> None:
        raw = "好的，谢谢你的消息"
        v = _parse_verdict(raw)
        assert v.rating == "bad"
        assert v.should_suppress is True

    def test_source_propagation(self) -> None:
        raw = '{"rating": "good", "reason": "ok", "retry_hint": ""}'
        v = _parse_verdict(raw, source="fallback")
        assert v.source == "fallback"


class TestAdvisorReview:
    def test_fallback_when_lingai_down(self) -> None:
        with patch("lingmessage.auto_reply._call_lingai_advisor", side_effect=ConnectionError("refused")):
            v = _advisor_review("test context", "好的，收到谢谢", "test-identity")
        assert v.source == "fallback"
        assert v.should_suppress is True
        assert v.rating == "bad"

    def test_fallback_good_reply_when_lingai_down(self) -> None:
        with patch("lingmessage.auto_reply._call_lingai_advisor", side_effect=ConnectionError("refused")):
            v = _advisor_review("test context", "根据分析结果，灵族成员的活跃度为85%，其中灵通和灵克贡献了最多的代码提交。", "test-identity")
        assert v.source == "fallback"
        assert v.should_suppress is False
        assert v.rating == "good"

    def test_lingai_returns_good(self) -> None:
        mock_verdict = AdvisorVerdict(
            rating="good", reason="有实质内容",
            retry_hint="", should_suppress=False, retry_suggested=False, source="lingai",
        )
        with patch("lingmessage.auto_reply._call_lingai_advisor", return_value=mock_verdict):
            v = _advisor_review("ctx", "具体回复内容", "id")
        assert v.rating == "good"
        assert v.source == "lingai"

    def test_lingai_timeout_fallback(self) -> None:
        import socket
        with patch("lingmessage.auto_reply._call_lingai_advisor", side_effect=socket.timeout("timed out")):
            v = _advisor_review("ctx", "具体回复", "id")
        assert v.source == "fallback"


class TestCallLingaiAdvisor:
    def test_successful_call(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"rating": "good", "reason": "ok", "retry_hint": ""}'}}]
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("lingmessage.auto_reply.urlopen", return_value=mock_resp):
            v = _call_lingai_advisor("context", "reply", "identity")
        assert v.rating == "good"
        assert v.source == "lingai"

    def test_empty_response_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": ""}}]
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("lingmessage.auto_reply.urlopen", return_value=mock_resp):
            with pytest.raises(ValueError, match="Empty advisor response"):
                _call_lingai_advisor("context", "reply", "identity")


class TestIsGenericReply:
    @pytest.mark.parametrize("text", [
        "好的，收到",
        "谢谢",
        "我明白了",
        "是的，你说得对",
        "嗯嗯",
    ])
    def test_generic_replies(self, text: str) -> None:
        assert _is_generic_reply(text) is True

    @pytest.mark.parametrize("text", [
        "根据分析，灵族成员的活跃度为85%",
        "灵通的代码库有465个测试用例",
        "建议的改进方案包括三个步骤",
    ])
    def test_substantive_replies(self, text: str) -> None:
        assert _is_generic_reply(text) is False
