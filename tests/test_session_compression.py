from __future__ import annotations

"""Tests for session_compression module — extracted from 灵克 compression logic."""

import pytest

from lingmessage.session_compression import (
    CompressionConfig,
    CompressionLevel,
    CompressionResult,
    auto_compress_history,
    compress_messages,
    extract_facts_from_messages,
    generate_chinese_summary,
    recall_facts,
)


class TestCompressionLevel:
    def test_values(self):
        assert CompressionLevel.TRUNCATE == "truncate"
        assert CompressionLevel.SUMMARY == "summary"
        assert CompressionLevel.AGGRESSIVE == "aggressive"

    def test_str_enum(self):
        assert CompressionLevel.TRUNCATE == "truncate"
        assert isinstance(CompressionLevel.SUMMARY, str)


class TestCompressionConfig:
    def test_defaults(self):
        cfg = CompressionConfig()
        assert cfg.max_messages == 24
        assert cfg.summary_max_chars == 4000
        assert cfg.archive_to_memory is True
        assert cfg.level == CompressionLevel.SUMMARY

    def test_custom(self):
        cfg = CompressionConfig(
            max_messages=10,
            level=CompressionLevel.AGGRESSIVE,
        )
        assert cfg.max_messages == 10
        assert cfg.level == CompressionLevel.AGGRESSIVE


class TestCompressionResult:
    def test_frozen(self):
        r = CompressionResult(
            compressed_messages=[],
            dropped_count=0,
            summary_text="",
            archived_facts=0,
            tokens_estimated_saved=0,
            level=CompressionLevel.SUMMARY,
        )
        with pytest.raises(AttributeError):
            r.dropped_count = 5


class TestExtractFacts:
    def test_string_messages(self):
        messages = [
            "读取了 config.yaml 文件内容",
            "决定采用 SQLite 作为存储方案",
            "排除了 Redis 方案",
            "遇到错误: connection refused",
        ]
        facts = extract_facts_from_messages(messages)
        assert "config.yaml" in facts["files_read"]
        assert any("SQLite" in d for d in facts["decisions"])
        assert any("Redis" in e for e in facts["exclusions"])
        assert any("connection" in e for e in facts["errors"])

    def test_dict_messages(self):
        messages = [
            {"role": "user", "content": "查看 main.py 的代码"},
            {"role": "assistant", "content": "决定使用 FastAPI"},
        ]
        facts = extract_facts_from_messages(messages)
        assert "main.py" in facts["files_read"]
        assert any("FastAPI" in d for d in facts["decisions"])

    def test_empty_messages(self):
        facts = extract_facts_from_messages([])
        assert facts == {"files_read": [], "decisions": [], "exclusions": [], "errors": []}

    def test_file_path_extraction(self):
        messages = [
            "修改了 src/api/router.py 和 tests/test_api.py",
            "also updated config.yaml",
        ]
        facts = extract_facts_from_messages(messages)
        assert "router.py" in " ".join(facts["files_read"])
        assert "config.yaml" in facts["files_read"]

    def test_short_lines_skipped(self):
        messages = ["决定 ok"]  # too short (< 10 chars stripped)
        facts = extract_facts_from_messages(messages)
        assert facts["decisions"] == []

    def test_http_urls_excluded(self):
        messages = ["访问了 http://example.com/config.yaml 获取配置"]
        facts = extract_facts_from_messages(messages)
        assert all("http" not in f for f in facts["files_read"])


class TestGenerateChineseSummary:
    def test_with_facts(self):
        facts = {
            "files_read": ["src/main.py", "config.yaml"],
            "decisions": ["采用 SQLite"],
            "exclusions": ["排除 Redis"],
            "errors": ["连接超时"],
        }
        summary = generate_chinese_summary(facts, dropped_count=10)
        assert "前 10 轮对话" in summary
        assert "已读文件" in summary
        assert "已做决策" in summary
        assert "已排除方案" in summary
        assert "已遇错误" in summary

    def test_empty_facts(self):
        facts = {"files_read": [], "decisions": [], "exclusions": [], "errors": []}
        summary = generate_chinese_summary(facts, dropped_count=5)
        assert "前 5 轮对话" in summary
        assert "已读文件" not in summary

    def test_with_recent_context(self):
        facts = {"files_read": [], "decisions": [], "exclusions": [], "errors": []}
        summary = generate_chinese_summary(facts, 3, recent_context="最近在做什么")
        assert "最近上下文片段" in summary


class TestCompressMessages:
    def test_below_threshold(self):
        messages = ["msg1", "msg2", "msg3"]
        result = compress_messages(messages)
        assert result.dropped_count == 0
        assert result.compressed_messages == messages

    def test_truncate_level(self):
        messages = [f"message {i}" for i in range(30)]
        config = CompressionConfig(max_messages=10, level=CompressionLevel.TRUNCATE)
        result = compress_messages(messages, config)
        assert result.dropped_count == 20
        assert result.summary_text == "[前 20 轮对话已压缩]"
        assert len(result.compressed_messages) == 11  # summary + 10 kept

    def test_summary_level(self):
        messages = [
            "读取了 src/main.py 文件",
            "决定使用 FastAPI 框架",
            *[f"普通消息 {i}" for i in range(25)],
        ]
        config = CompressionConfig(max_messages=10, level=CompressionLevel.SUMMARY)
        result = compress_messages(messages, config)
        assert result.dropped_count > 0
        assert "压缩摘要" in result.summary_text
        assert result.archived_facts > 0

    def test_summary_max_chars(self):
        messages = [f"决定采用方案{i}来处理这个非常复杂的问题需要仔细考虑" for i in range(50)]
        config = CompressionConfig(
            max_messages=5,
            summary_max_chars=200,
            level=CompressionLevel.SUMMARY,
        )
        result = compress_messages(messages, config)
        assert len(result.summary_text) <= 230  # 200 + truncation suffix

    def test_tokens_estimated_saved(self):
        messages = ["a" * 100 for _ in range(30)]
        config = CompressionConfig(max_messages=10)
        result = compress_messages(messages, config)
        assert result.tokens_estimated_saved > 0


class TestRecallFacts:
    def test_basic_recall(self):
        sessions = [
            {
                "member_id": "lingclaude",
                "facts": {
                    "files_read": ["src/main.py", "config.yaml"],
                    "decisions": ["采用 SQLite 方案"],
                    "exclusions": [],
                    "errors": [],
                },
            },
        ]
        results = recall_facts(sessions, "SQLite")
        assert len(results) >= 1
        assert results[0]["fact"] == "采用 SQLite 方案"
        assert results[0]["member_id"] == "lingclaude"

    def test_member_filter(self):
        sessions = [
            {
                "member_id": "lingclaude",
                "facts": {
                    "files_read": [],
                    "decisions": ["采用 SQLite"],
                    "exclusions": [],
                    "errors": [],
                },
            },
            {
                "member_id": "lingflow",
                "facts": {
                    "files_read": [],
                    "decisions": ["采用 PostgreSQL"],
                    "exclusions": [],
                    "errors": [],
                },
            },
        ]
        results = recall_facts(sessions, "采用", member_filter="lingflow")
        assert len(results) == 1
        assert results[0]["member_id"] == "lingflow"

    def test_limit(self):
        sessions = [
            {
                "member_id": "test",
                "facts": {
                    "files_read": [],
                    "decisions": ["决定A", "决定B", "决定C"],
                    "exclusions": [],
                    "errors": [],
                },
            },
        ]
        results = recall_facts(sessions, "决定", limit=2)
        assert len(results) == 2

    def test_no_match(self):
        sessions = [
            {
                "member_id": "test",
                "facts": {
                    "files_read": ["config.yaml"],
                    "decisions": ["采用方案"],
                    "exclusions": [],
                    "errors": [],
                },
            },
        ]
        results = recall_facts(sessions, "nonexistent_xyz")
        assert results == []

    def test_extract_from_messages_when_no_facts(self):
        sessions = [
            {
                "member_id": "test",
                "messages": [
                    "读取了 handler.py 文件",
                    "决定使用 async 模式",
                ],
            },
        ]
        results = recall_facts(sessions, "handler")
        assert len(results) >= 1
        assert "handler.py" in results[0]["fact"]

    def test_case_insensitive(self):
        sessions = [
            {
                "member_id": "test",
                "facts": {
                    "files_read": [],
                    "decisions": ["Decided to use SQLite"],
                    "exclusions": [],
                    "errors": [],
                },
            },
        ]
        results = recall_facts(sessions, "sqlite")
        assert len(results) == 1


class TestAutoCompressHistory:
    def _make_history(self, n):
        return [{"role": "user", "content": f"message {i} decided to use pytest"} for i in range(n)]

    def test_default_summary_is_system_role_dict(self):
        history = self._make_history(30)
        config = CompressionConfig(max_messages=10)
        compressed, state = auto_compress_history(history, config=config)
        assert len(compressed) == 11
        assert compressed[0] == {"role": "system", "content": compressed[0]["content"]}

    def test_custom_summary_wrapper(self):
        history = self._make_history(30)
        config = CompressionConfig(max_messages=10)
        wrapper = lambda txt: {"speaker": "narrator", "content": txt}
        compressed, state = auto_compress_history(history, config=config, summary_wrapper=wrapper)
        assert len(compressed) == 11
        assert compressed[0]["speaker"] == "narrator"
        assert "压缩" in compressed[0]["content"]

    def test_no_wrapper_below_threshold(self):
        history = self._make_history(5)
        config = CompressionConfig(max_messages=10)
        compressed, state = auto_compress_history(history, config=config, summary_wrapper=lambda t: t)
        assert len(compressed) == 5
        assert "_compression_facts" not in state

    def test_none_config_no_compression(self):
        history = self._make_history(100)
        compressed, state = auto_compress_history(history, config=None)
        assert len(compressed) == 100
