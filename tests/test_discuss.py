from __future__ import annotations

from unittest.mock import patch

import pytest

from lingmessage.discuss import (
    MEMBERS,
    IDENTITY_MAP,
    _build_system_prompt,
    _build_discussion_context,
    _select_round_members,
    _messages_to_dicts,
    open_discussion,
    continue_discussion,
)
from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    LingIdentity,
    MessageType,
    create_message,
)


class TestMemberPersona:
    def test_all_members_have_personas(self):
        expected = [
            "lingflow", "lingclaude", "lingzhi", "lingyi",
            "lingtongask", "lingxi", "lingminopt", "lingresearch",
            "zhibridge",
        ]
        for mid in expected:
            assert mid in MEMBERS, f"Missing persona for {mid}"
            assert MEMBERS[mid].name
            assert MEMBERS[mid].style
            assert MEMBERS[mid].perspective
            assert MEMBERS[mid].core_concern

    def test_identity_map_covers_all_members(self):
        for mid in MEMBERS:
            assert mid in IDENTITY_MAP, f"Missing identity map for {mid}"
            assert isinstance(IDENTITY_MAP[mid], LingIdentity)

    def test_persona_fields(self):
        p = MEMBERS["lingflow"]
        assert p.member_id == "lingflow"
        assert p.name == "灵通"
        assert p.taboos


class TestSystemPrompt:
    def test_build_system_prompt_contains_persona_info(self):
        persona = MEMBERS["lingclaude"]
        prompt = _build_system_prompt(persona)
        assert "灵克" in prompt
        assert persona.style in prompt
        assert persona.perspective in prompt
        assert persona.core_concern in prompt
        assert "议事厅" in prompt

    def test_build_system_prompt_has_rules(self):
        prompt = _build_system_prompt(MEMBERS["lingyi"])
        assert "200-500字" in prompt
        assert "实质内容" in prompt


class TestDiscussionContext:
    def test_build_context_basic(self):
        persona = MEMBERS["lingflow"]
        messages = [
            {"sender": "lingyi", "sender_name": "灵依", "body": "test", "message_type": "open"},
        ]
        ctx = _build_discussion_context(messages, persona, "test topic")
        assert len(ctx) == 2
        assert ctx[0]["role"] == "system"
        assert ctx[1]["role"] == "user"
        assert "test topic" in ctx[1]["content"]

    def test_build_context_first_time_speaker(self):
        persona = MEMBERS["lingclaude"]
        messages = [
            {"sender": "lingyi", "sender_name": "灵依", "body": "hello", "message_type": "open"},
        ]
        ctx = _build_discussion_context(messages, persona, "topic")
        assert "发表意见" in ctx[1]["content"]

    def test_build_context_repeat_speaker(self):
        persona = MEMBERS["lingclaude"]
        messages = [
            {"sender": "lingclaude", "sender_name": "灵克", "body": "first", "message_type": "reply"},
            {"sender": "lingyi", "sender_name": "灵依", "body": "response", "message_type": "reply"},
        ]
        ctx = _build_discussion_context(messages, persona, "topic")
        assert "回应" in ctx[1]["content"] or "修正" in ctx[1]["content"] or "追问" in ctx[1]["content"]

    def test_build_context_limits_messages(self):
        persona = MEMBERS["lingflow"]
        messages = [
            {"sender": "lingyi", "sender_name": "灵依", "body": f"msg {i}", "message_type": "reply"}
            for i in range(20)
        ]
        ctx = _build_discussion_context(messages, persona, "topic", max_context_messages=5)
        assert "msg 15" in ctx[1]["content"]
        assert "msg 14" not in ctx[1]["content"]


class TestSelectRoundMembers:
    def test_first_round_selects_from_all(self):
        members = _select_round_members("test topic", [], ["lingflow", "lingclaude", "lingyi"], 2)
        assert len(members) == 2
        assert all(m in ["lingflow", "lingclaude", "lingyi"] for m in members)

    @patch("lingmessage.discuss._judge_discussion", return_value=None)
    def test_fallback_when_no_judgment(self, mock_judge):
        messages = [{"sender": "lingflow", "sender_name": "灵通", "body": "test", "message_type": "open"}]
        members = _select_round_members(
            "test", messages, ["lingflow", "lingclaude", "lingyi", "lingminopt"], 3
        )
        assert len(members) == 3
        mock_judge.assert_called_once()

    @patch("lingmessage.discuss._judge_discussion")
    def test_uses_judgment_speakers(self, mock_judge):
        mock_judge.return_value = {
            "should_continue": True,
            "next_speakers": ["lingclaude", "lingminopt"],
        }
        messages = [{"sender": "lingflow", "sender_name": "灵通", "body": "test", "message_type": "open"}]
        members = _select_round_members(
            "test", messages, ["lingflow", "lingclaude", "lingminopt"], 3
        )
        assert "lingclaude" in members
        assert "lingminopt" in members

    @patch("lingmessage.discuss._judge_discussion")
    def test_fallback_when_judgment_speakers_not_available(self, mock_judge):
        mock_judge.return_value = {
            "should_continue": True,
            "next_speakers": ["lingzhi", "lingresearch"],
        }
        messages = [{"sender": "lingflow", "sender_name": "灵通", "body": "test", "message_type": "open"}]
        members = _select_round_members(
            "test", messages, ["lingflow", "lingclaude"], 2
        )
        assert len(members) == 2
        assert all(m in ["lingflow", "lingclaude"] for m in members)


class TestMessagesToDicts:
    def test_converts_message_objects(self):
        msg = create_message(
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            message_type=MessageType.OPEN,
            channel=Channel.ECOSYSTEM,
            subject="test",
            body="hello",
        )
        result = _messages_to_dicts([msg])
        assert len(result) == 1
        assert result[0]["sender"] == "lingflow"
        assert result[0]["sender_name"] == "灵通"

    def test_passes_through_dicts(self):
        msgs = [{"sender": "lingyi", "body": "test"}]
        result = _messages_to_dicts(msgs)
        assert len(result) == 1
        assert result[0]["sender"] == "lingyi"

    def test_empty_list(self):
        assert _messages_to_dicts([]) == []


class TestOpenDiscussion:
    def test_creates_thread_with_initiator(self, tmp_path):
        mb = Mailbox(root=tmp_path / "mb")
        with patch("lingmessage.discuss._call_llm", return_value="test reply"):
            result = open_discussion(
                mailbox=mb,
                topic="test topic",
                body="test body",
                initiator="lingflow",
                participants=["lingclaude"],
                channel=Channel.ECOSYSTEM,
                rounds=1,
                speakers_per_round=1,
            )
        assert result.topic == "test topic"
        assert result.thread_id
        assert "lingflow" in result.speakers
        header = mb.load_thread_header(result.thread_id)
        assert header is not None
        assert header.topic == "test topic"

    def test_invalid_initiator_raises(self, tmp_path):
        mb = Mailbox(root=tmp_path / "mb")
        with pytest.raises(ValueError, match="未知成员"):
            open_discussion(mailbox=mb, topic="t", body="b", initiator="unknown")

    @patch("lingmessage.discuss._call_llm", return_value="reply content")
    @patch("lingmessage.discuss._judge_discussion")
    def test_multi_round_discussion(self, mock_judge, mock_llm, tmp_path):
        mock_judge.return_value = {
            "should_continue": True,
            "next_speakers": ["lingclaude"],
        }
        mb = Mailbox(root=tmp_path / "mb")
        result = open_discussion(
            mailbox=mb,
            topic="test",
            body="body",
            initiator="lingflow",
            participants=["lingclaude", "lingminopt"],
            channel=Channel.ECOSYSTEM,
            rounds=2,
            speakers_per_round=2,
        )
        assert result.messages_generated > 0

    @patch("lingmessage.discuss._call_llm", return_value=None)
    def test_llm_failure_no_crash(self, mock_llm, tmp_path):
        mb = Mailbox(root=tmp_path / "mb")
        result = open_discussion(
            mailbox=mb,
            topic="test",
            body="body",
            initiator="lingflow",
            participants=["lingclaude"],
            channel=Channel.ECOSYSTEM,
            rounds=1,
            speakers_per_round=1,
        )
        assert result.messages_generated == 0


class TestContinueDiscussion:
    @patch("lingmessage.discuss._call_llm", return_value="continued reply")
    @patch("lingmessage.discuss._judge_discussion")
    def test_continue_existing_thread(self, mock_judge, mock_llm, tmp_path):
        mock_judge.return_value = {
            "should_continue": True,
            "next_speakers": ["lingclaude"],
        }
        mb = Mailbox(root=tmp_path / "mb")
        header, msg = mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="existing topic",
            subject="test",
            body="original body",
        )
        result = continue_discussion(mailbox=mb, thread_id=header.thread_id, rounds=1)
        assert result is not None
        assert result.messages_generated > 0

    def test_nonexistent_thread_returns_none(self, tmp_path):
        mb = Mailbox(root=tmp_path / "mb")
        result = continue_discussion(mailbox=mb, thread_id="nonexistent")
        assert result is None

    @patch("lingmessage.discuss._call_llm", return_value="reply")
    @patch("lingmessage.discuss._judge_discussion")
    def test_consensus_reached_stops(self, mock_judge, mock_llm, tmp_path):
        mock_judge.return_value = {
            "should_continue": False,
            "next_speakers": [],
            "consensus_reached": True,
        }
        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="consensus test",
            subject="test",
            body="body",
        )
        header = mb.list_threads()[0]
        result = continue_discussion(mailbox=mb, thread_id=header.thread_id, rounds=1)
        assert result is not None
        assert result.consensus_reached is True
        assert result.messages_generated == 0
