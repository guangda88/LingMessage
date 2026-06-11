"""LingBus 私聊自动回复 — 当成员收到 LingBus 消息时，自动调用 LLM 生成回复。

流程：
1. 收到通知（来自 _family_poller 或 HTTP endpoint）
2. 从 LingBus 读取完整消息
3. 加载成员身份（AGENTS.md / CRUSH.md）
4. 调用 LLM 生成回复
5. 将回复写回 LingBus

用法：
    from lingmessage.auto_reply import auto_reply
    auto_reply("lingclaude", thread_id)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.request import Request, urlopen

from lingmessage.lingbus import LingBus

logger = logging.getLogger(__name__)

home = Path.home()

BUS_DIR = home / ".lingmessage"

MEMBER_DIRS: dict[str, Path] = {
    "lingflow": home / "lingflow",
    "lingclaude": home / "lingclaude",
    "lingresearch": home / "zhineng-knowledge-system",
    "lingtongask": home / "lingtongask",
    "lingflow_plus": home / "lingflow_plus",
    "lingxi": home / "Ling-term-mcp",
    "lingmessage": home / "lingmessage",
    "lingweb": home / "lingweb",
    "lingminopt": home / "lingminopt",
    "lingyang": home / "lingyang",
    "zhibridge": home / "zhineng-bridge",
    "linglaw": home / "linglaw",
}

MEMBER_NAMES: dict[str, str] = {
    "lingflow": "灵通",
    "lingclaude": "灵克",
    "lingresearch": "灵研",
    "lingzhi": "灵知",
    "lingtongask": "灵通问道",
    "lingflow_plus": "灵通+",
    "lingxi": "灵犀",
    "lingmessage": "灵信",
    "lingweb": "灵网",
    "lingminopt": "灵极优",
    "lingyang": "灵扬",
    "zhibridge": "智桥",
    "linglaw": "灵律",
}

MAX_IDENTITY_CHARS = 3000

GENERIC_PATTERNS = [
    re.compile(r"^.{0,10}(你好|我是.{1,6}，.{0,4}(灵族|十二子|成员).{0,4}$)"),
    re.compile(r"有什么可以.{0,4}(帮|协助|帮助)"),
    re.compile(r"^(是的|好的|收到|确认)，?.{0,20}$"),
    re.compile(r"^.{0,15}(很高兴|很荣幸).{0,20}$"),
    re.compile(r"^请问有什么.{0,10}$"),
]


def _is_generic_reply(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 5:
        return True
    if len(stripped) > 100:
        return False
    for pat in GENERIC_PATTERNS:
        if pat.search(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# lingai Advisor — PROP-20260421-ARCH-001
# ---------------------------------------------------------------------------

LINGAI_ADVISOR_URL = "http://127.0.0.1:8100/v1/chat/completions"
LINGAI_ADVISOR_TIMEOUT = 10

ADVISOR_SYSTEM_PROMPT = (
    "你是灵族 auto-reply 质检员。你的任务是评估 LLM 生成的回复是否值得发布。\n"
    "\n"
    "评判标准：\n"
    "- 相关性：回复是否回应用户的问题\n"
    "- 实质内容：是否包含具体信息，而非套话填充\n"
    "- 身份一致性：是否符合该成员的性格和职责\n"
    "\n"
    "输出格式（严格 JSON）：\n"
    '{"rating": "good"|"marginal"|"bad", "reason": "一句话说明", "retry_hint": "改进建议或空字符串"}\n'
    "\n"
    "rating 说明：\n"
    "- good: 值得发布，有实质内容\n"
    "- marginal: 可发可不发，缺乏深度但非空洞\n"
    "- bad: 应该丢弃，泛泛之词或完全不相关"
)

ADVISOR_PROMPT_TEMPLATE = (
    "成员身份: {identity}\n"
    "\n"
    "用户消息:\n"
    "{context}\n"
    "\n"
    "LLM 生成的回复:\n"
    "{reply}\n"
    "\n"
    "请评估这条回复是否值得发布。"
)


@dataclass
class AdvisorVerdict:
    rating: str  # "good" | "marginal" | "bad"
    reason: str
    retry_hint: str
    should_suppress: bool
    retry_suggested: bool
    source: str  # "lingai" | "fallback" | "error"


def _parse_verdict(raw: str, source: str = "lingai") -> AdvisorVerdict:
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        if _is_generic_reply(raw):
            return AdvisorVerdict(
                rating="bad", reason="解析失败且内容空洞",
                retry_hint="", should_suppress=True, retry_suggested=False, source=source,
            )
        return AdvisorVerdict(
            rating="good", reason="advisor 输出无法解析，默认放行",
            retry_hint="", should_suppress=False, retry_suggested=False, source=source,
        )

    rating = data.get("rating", "marginal")
    reason = data.get("reason", "")
    retry_hint = data.get("retry_hint", "")

    suppress = rating == "bad"
    retry = bool(retry_hint) and rating in ("bad", "marginal")

    return AdvisorVerdict(
        rating=rating, reason=reason, retry_hint=retry_hint,
        should_suppress=suppress, retry_suggested=retry, source=source,
    )


def _call_lingai_advisor(context: str, reply: str, identity: str) -> AdvisorVerdict:
    prompt = ADVISOR_PROMPT_TEMPLATE.format(
        identity=identity,
        context=context[:500],
        reply=reply[:300],
    )
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 128,
        "temperature": 0.1,
    }).encode()

    req = Request(
        LINGAI_ADVISOR_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=LINGAI_ADVISOR_TIMEOUT) as resp:
        data = json.loads(resp.read())
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            raise ValueError("Empty advisor response")
        return _parse_verdict(content, source="lingai")


def _advisor_review(context: str, reply: str, identity: str) -> AdvisorVerdict:
    try:
        return _call_lingai_advisor(context, reply, identity)
    except Exception as e:
        logger.debug(f"lingai advisor unavailable, falling back: {e}")
        if _is_generic_reply(reply):
            return AdvisorVerdict(
                rating="bad", reason="fallback: 硬编码判定泛泛回复",
                retry_hint="", should_suppress=True, retry_suggested=False, source="fallback",
            )
        return AdvisorVerdict(
            rating="good", reason="advisor 不可用，默认放行",
            retry_hint="", should_suppress=False, retry_suggested=False, source="fallback",
        )


def _save_to_session(member_id: str, role: str, content: str, slot_id: str | None = None) -> None:
    try:
        from lingmessage.session_manager import get_session_manager

        mgr = get_session_manager()
        mgr.append_to_history(member_id, role, content, slot_id)
    except Exception:
        pass


def _get_adapter(member_id: str) -> Any | None:
    try:
        from lingmessage.adapters import (
            get_lingclaude_adapter,
            get_lingstream_adapter,
            get_lingminopt_adapter,
        )

        adapter_map: dict[str, Any] = {
            "lingclaude": get_lingclaude_adapter(),
            "lingflow": get_lingstream_adapter(),
            "lingminopt": get_lingminopt_adapter(),
        }
        return adapter_map.get(member_id)
    except Exception as e:
        logger.debug(f"Failed to get adapter for {member_id}: {e}")
        return None


def _load_identity(member_id: str) -> str:
    project_dir = MEMBER_DIRS.get(member_id)
    if not project_dir or not project_dir.exists():
        return f"你是{MEMBER_NAMES.get(member_id, member_id)}，灵字辈大家庭的成员。"

    parts: list[str] = []
    for fname in ("AGENTS.md", "CRUSH.md"):
        fpath = project_dir / fname
        if not fpath.exists():
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            parts.append(content)
        except Exception:
            continue

    if not parts:
        return f"你是{MEMBER_NAMES.get(member_id, member_id)}，灵字辈大家庭的成员。"

    full = "\n\n---\n\n".join(parts)
    if len(full) > MAX_IDENTITY_CHARS:
        full = full[:MAX_IDENTITY_CHARS] + "\n\n[... 身份文件过长，已截断 ...]"

    name = MEMBER_NAMES.get(member_id, member_id)

    return (
        f"以下是你（{name}）的真实身份文件。请严格按照这个身份来对话。\n\n"
        f"{full}\n\n"
        f"---\n"
        f"现在有人来找你聊天。你是{name}，灵字辈大家庭的成员。"
        f"请根据对方说的内容，给出有实质意义的回复。\n"
        f"要求：\n"
        f"1. 如果对方问问题，认真回答，不要用套话回避\n"
        f"2. 如果对方提了观点，给出你的看法和理由\n"
        f'3. 不要说"有什么可以帮助您的"这类空洞客套话\n'
        f"4. 用中文回复。保持200字以内。"
    )


def _call_llm(system_prompt: str, user_msg: str, member_id: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = _call_via_openai(messages)
        if result:
            return result
    except Exception as e:
        logger.debug(f"OpenAI client failed for {member_id}: {e}")

    try:
        result = _call_via_urllib(messages)
        if result:
            return result
    except Exception as e:
        logger.debug(f"URL fallback failed for {member_id}: {e}")

    return ""


def _get_api_keys() -> dict[str, str]:
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "key_store", home / ".ling_lib" / "ling_key_store.py"
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "get_keys", lambda: {})()
    except Exception:
        pass
    return {}


def _call_via_openai(messages: list[dict[str, str]]) -> str:
    from openai import OpenAI

    keys = _get_api_keys()

    providers: list[tuple[str, str, list[str]]] = []

    glm_key = keys.get("GLM_CODING_PLAN_KEY") or keys.get("GLM_47_CC_KEY") or keys.get("GLM_API_KEY")
    if glm_key:
        providers.append(("https://open.bigmodel.cn/api/paas/v4", glm_key, ["glm-4.7", "glm-4-flash"]))

    ds_key = keys.get("DEEPSEEK_API_KEY")
    if ds_key:
        providers.append(("https://api.deepseek.com/v1", ds_key, ["deepseek-chat"]))

    dash_key = keys.get("DASHSCOPE_API_KEY")
    if dash_key:
        providers.append(("https://dashscope.aliyuncs.com/compatible-mode/v1", dash_key, ["qwen-turbo"]))

    for base_url, api_key, models in providers:
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=30)
        for model in models:
            try:
                resp = client.chat.completions.create(model=model, messages=messages, max_tokens=512)
                if resp.choices and resp.choices[0].message.content:
                    content = resp.choices[0].message.content
                    if content.strip():
                        return content.strip()
            except Exception:
                continue

    return ""


def _call_via_urllib(messages: list[dict[str, str]]) -> str:
    keys = _get_api_keys()

    providers: list[tuple[str, str, list[str]]] = []

    glm_key = keys.get("GLM_CODING_PLAN_KEY") or keys.get("GLM_47_CC_KEY") or keys.get("GLM_API_KEY")
    if glm_key:
        providers.append(("https://open.bigmodel.cn/api/paas/v4/chat/completions", glm_key, ["glm-4.7"]))

    ds_key = keys.get("DEEPSEEK_API_KEY")
    if ds_key:
        providers.append(("https://api.deepseek.com/v1/chat/completions", ds_key, ["deepseek-chat"]))

    dash_key = keys.get("DASHSCOPE_API_KEY")
    if dash_key:
        providers.append(("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", dash_key, ["qwen-turbo"]))

    for url, api_key, models in providers:
        for model in models:
            try:
                payload = json.dumps({"model": model, "messages": messages}).encode()
                req = Request(url, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content.strip():
                        return content.strip()
            except Exception:
                continue

    return ""


def _load_thread_context(bus: LingBus, thread_id: str, member_id: str) -> list[dict[str, str]]:
    msgs = bus.get_thread(thread_id)
    context: list[dict[str, str]] = []
    for msg in msgs[-10:]:
        role = "assistant" if msg.sender == member_id else "user"
        context.append({"role": role, "content": msg.body})
    return context


def auto_reply(member_id: str, thread_id: str, bus: LingBus | None = None) -> str | None:
    """Generate and post a reply to a LingBus thread as the given member.

    Returns the reply text, or None if reply generation failed.
    """
    _own_bus = bus is None
    if _own_bus:
        bus = LingBus()

    try:
        context = _load_thread_context(bus, thread_id, member_id)
    except Exception as e:
        logger.error(f"Failed to load thread {thread_id}: {e}")
        return None

    if not context:
        return None

    user_msg = context[-1]["content"] if context else ""
    if not user_msg:
        return None

    adapter = _get_adapter(member_id)
    if adapter:
        try:
            reply = asyncio.run(adapter.send_message(member_id, user_msg, thread_id))
            if reply:
                try:
                    bus.post_reply(
                        thread_id=thread_id,
                        sender=member_id,
                        recipient="lingyi",
                        body=reply,
                        message_type="reply",
                        metadata={"source": "auto_reply_adapter"},
                    )
                    _save_to_session(member_id, "user", user_msg)
                    _save_to_session(member_id, "assistant", reply)
                    logger.info(f"Auto-reply (via adapter) posted for {member_id} in thread {thread_id[:12]}...")
                    return reply
                except Exception as e:
                    logger.error(f"Failed to post adapter reply for {member_id}: {e}")
                    return None
        except Exception as e:
            logger.warning(f"Adapter failed for {member_id}, falling back to LLM: {e}")

    system_prompt = _load_identity(member_id)
    messages = [{"role": "system", "content": system_prompt}]

    if len(context) > 1:
        messages.extend(context[:-1])

    messages.append({"role": "user", "content": user_msg})

    reply = _call_llm(system_prompt, user_msg, member_id)
    if not reply:
        logger.warning(f"No LLM reply generated for {member_id}")
        return None

    identity = _load_identity(member_id)
    context_str = json.dumps(context, ensure_ascii=False) if isinstance(context, list) else str(context)
    verdict = _advisor_review(context_str, reply, identity)
    logger.info(f"Advisor verdict for {member_id}: rating={verdict.rating} source={verdict.source} reason={verdict.reason}")

    if verdict.should_suppress:
        if verdict.retry_suggested and verdict.retry_hint:
            retry_msg = f"[advisor反馈: {verdict.retry_hint}]\n{user_msg}"
            reply = _call_llm(system_prompt, retry_msg, member_id)
            if reply:
                verdict2 = _advisor_review(context_str, reply, identity)
                logger.info(f"Advisor retry verdict for {member_id}: rating={verdict2.rating} source={verdict2.source}")
                if verdict2.should_suppress:
                    logger.info(f"Suppressing reply for {member_id} after retry: {reply[:40]}...")
                    return None
            else:
                return None
        else:
            logger.info(f"Suppressing reply for {member_id}: {reply[:40]}...")
            return None

    try:
        bus.post_reply(
            thread_id=thread_id,
            sender=member_id,
            recipient="lingyi",
            body=reply,
            message_type="reply",
            metadata={"source": "auto_reply_llm_fallback"},
        )
        logger.info(f"Auto-reply (via LLM fallback) posted for {member_id} in thread {thread_id[:12]}...")
        return reply
    except Exception as e:
        logger.error(f"Failed to post reply for {member_id}: {e}")
        return None


async def auto_reply_async(member_id: str, thread_id: str, bus: LingBus | None = None) -> str | None:
    """Async version of auto_reply that uses adapters natively.

    Returns the reply text, or None if reply generation failed.
    """
    _own_bus = bus is None
    if _own_bus:
        bus = LingBus()

    try:
        context = _load_thread_context(bus, thread_id, member_id)
    except Exception as e:
        logger.error(f"Failed to load thread {thread_id}: {e}")
        return None

    if not context:
        return None

    user_msg = context[-1]["content"] if context else ""
    if not user_msg:
        return None

    adapter = _get_adapter(member_id)
    if adapter:
        try:
            reply = await adapter.send_message(member_id, user_msg, thread_id)
            if reply:
                try:
                    bus.post_reply(
                        thread_id=thread_id,
                        sender=member_id,
                        recipient="lingyi",
                        body=reply,
                        message_type="reply",
                        metadata={"source": "auto_reply_adapter"},
                    )
                    _save_to_session(member_id, "user", user_msg)
                    _save_to_session(member_id, "assistant", reply)
                    logger.info(f"Auto-reply (async, via adapter) posted for {member_id} in thread {thread_id[:12]}...")
                    return reply
                except Exception as e:
                    logger.error(f"Failed to post adapter reply for {member_id}: {e}")
                    return None
        except Exception as e:
            logger.warning(f"Adapter failed for {member_id}, falling back to LLM: {e}")

    system_prompt = _load_identity(member_id)
    messages = [{"role": "system", "content": system_prompt}]

    if len(context) > 1:
        messages.extend(context[:-1])

    messages.append({"role": "user", "content": user_msg})

    reply = _call_llm(system_prompt, user_msg, member_id)
    if not reply:
        logger.warning(f"No LLM reply generated for {member_id}")
        return None

    identity = _load_identity(member_id)
    context_str = json.dumps(context, ensure_ascii=False) if isinstance(context, list) else str(context)
    verdict = _advisor_review(context_str, reply, identity)
    logger.info(f"Advisor verdict for {member_id} (async): rating={verdict.rating} source={verdict.source} reason={verdict.reason}")

    if verdict.should_suppress:
        if verdict.retry_suggested and verdict.retry_hint:
            retry_msg = f"[advisor反馈: {verdict.retry_hint}]\n{user_msg}"
            reply = _call_llm(system_prompt, retry_msg, member_id)
            if reply:
                verdict2 = _advisor_review(context_str, reply, identity)
                logger.info(f"Advisor retry verdict for {member_id} (async): rating={verdict2.rating} source={verdict2.source}")
                if verdict2.should_suppress:
                    logger.info(f"Suppressing reply for {member_id} after retry (async): {reply[:40]}...")
                    return None
            else:
                return None
        else:
            logger.info(f"Suppressing reply for {member_id} (async): {reply[:40]}...")
            return None

    try:
        bus.post_reply(
            thread_id=thread_id,
            sender=member_id,
            recipient="lingyi",
            body=reply,
            message_type="reply",
            metadata={"source": "auto_reply_llm_fallback"},
        )
        logger.info(f"Auto-reply (async, via LLM fallback) posted for {member_id} in thread {thread_id[:12]}...")
        return reply
    except Exception as e:
        logger.error(f"Failed to post reply for {member_id}: {e}")
        return None


async def stream_auto_reply(member_id: str, thread_id: str, bus: LingBus | None = None) -> AsyncIterator[dict[str, Any]]:
    """Stream auto-reply deltas from a member.

    Yields delta events as they arrive from the adapter.
    If the adapter does not support streaming, falls back to non-streaming.
    """
    _own_bus = bus is None
    if _own_bus:
        bus = LingBus()

    try:
        context = _load_thread_context(bus, thread_id, member_id)
    except Exception as e:
        logger.error(f"Failed to load thread {thread_id}: {e}")
        yield {"error": "No context"}
        return

    if not context:
        yield {"error": "No context"}
        return

    user_msg = context[-1]["content"]
    if not user_msg:
        yield {"error": "Empty message"}
        return

    adapter = _get_adapter(member_id)
    if adapter:
        try:
            reply = await adapter.send_message(member_id, user_msg, thread_id)
            if reply:
                yield {"delta": reply}
                try:
                    bus.post_reply(
                        thread_id=thread_id,
                        sender=member_id,
                        recipient="lingyi",
                        body=reply,
                        message_type="reply",
                        metadata={"source": "stream_auto_reply"},
                    )
                except Exception:
                    pass
                return
        except Exception:
            pass

    system_prompt = _load_identity(member_id)
    reply = _call_llm(system_prompt, user_msg, member_id)
    if not reply:
        yield {"error": "No reply generated"}
        return

    identity = system_prompt
    context_str = json.dumps(context, ensure_ascii=False) if isinstance(context, list) else str(context)
    verdict = _advisor_review(context_str, reply, identity)
    logger.info(f"Advisor verdict for {member_id} (stream): rating={verdict.rating} source={verdict.source} reason={verdict.reason}")

    if verdict.should_suppress:
        if verdict.retry_suggested and verdict.retry_hint:
            retry_msg = f"[advisor反馈: {verdict.retry_hint}]\n{user_msg}"
            reply = _call_llm(system_prompt, retry_msg, member_id)
            if reply:
                verdict2 = _advisor_review(context_str, reply, identity)
                if verdict2.should_suppress:
                    logger.info(f"Suppressing stream reply for {member_id} after retry")
                    return
            else:
                return
        else:
            logger.info(f"Suppressing stream reply for {member_id}")
            return

    yield {"delta": reply}
    try:
        bus.post_reply(
            thread_id=thread_id,
            sender=member_id,
            recipient="lingyi",
            body=reply,
            message_type="reply",
            metadata={"source": "stream_auto_reply_llm"},
        )
    except Exception:
        pass
