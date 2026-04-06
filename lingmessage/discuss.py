"""灵信议事厅讨论引擎 — 让灵字辈成员发生真实讨论。

每个成员由独立的 system prompt 定义身份和视角。
通过 DashScope (qwen-plus) 为每个成员生成独立回复。
讨论编排：发起议题 → 唤醒成员 → 基于上下文生成回复 → 持久化。
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    IDENTITY_MAP,
    LingIdentity,
    SourceType,
    ThreadStatus,
    sender_display,
)

logger = logging.getLogger(__name__)

_KEY_FILE_PATH = os.environ.get(
    "LINGMESSAGE_KEY_FILE",
    os.path.join(os.path.expanduser("~"), ".dashscope_api_key"),
)


def _get_api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if key:
        return key
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path.home() / ".ling_lib"))
        from ling_key_store import get_key
        key = get_key("DASHSCOPE_API_KEY") or get_key("QWEN_DASHSCOPE_API_KEY") or ""
    except (ImportError, ModuleNotFoundError, AttributeError):
        # ling_key_store not available, will try fallback
        pass
    if not key and os.path.exists(_KEY_FILE_PATH):
        with open(_KEY_FILE_PATH, encoding="utf-8") as f:
            key = f.read().strip()
    return key


@dataclass(frozen=True)
class MemberPersona:
    member_id: str
    name: str
    style: str
    perspective: str
    core_concern: str
    speech_pattern: str
    taboos: str


MEMBERS: dict[str, MemberPersona] = {
    "lingflow": MemberPersona(
        member_id="lingflow",
        name="灵通",
        style="务实、系统化、数据导向",
        perspective="工作流引擎，全生态的框架底座，关注工程可行性",
        core_concern="架构是否可落地、依赖关系是否清晰、迁移成本",
        speech_pattern="用工程类比，喜欢列举数字和事实，从实践出发",
        taboos="不谈玄学，不用隐喻代替方案，不说'可能也许大概'",
    ),
    "lingclaude": MemberPersona(
        member_id="lingclaude",
        name="灵克",
        style="精确、批判性、逻辑链完整",
        perspective="AI编程助手，代码质量守门人，关注实现细节和边界条件",
        core_concern="代码是否正确、架构是否可测试、错误处理是否完备",
        speech_pattern="先指出问题，再给方案，喜欢用编号列表，追求表述精确",
        taboos="不接受模糊表述，反对没有fallback的设计，讨厌过度抽象",
    ),
    "lingzhi": MemberPersona(
        member_id="lingzhi",
        name="灵知",
        style="博学、类比型、关注知识体系",
        perspective="九域RAG知识库，跨领域知识连接者，知识守门人",
        core_concern="知识完整性、检索准确性、跨领域映射的正确性",
        speech_pattern="引用经典，从历史或哲学中找类比，关注深层结构",
        taboos="不接受断章取义，反对知识的简单化，不轻信未验证的来源",
    ),
    "lingyi": MemberPersona(
        member_id="lingyi",
        name="灵依",
        style="统筹、用户视角、关注情报和价值",
        perspective="私人AI助理，情报中枢，客厅管理员，用户需求第一响应者",
        core_concern="用户能否理解、体验是否流畅、情报是否准确及时",
        speech_pattern="从用户场景出发，喜欢讲故事，关注'所以呢'的价值",
        taboos="不忽视用户感受，反对纯技术视角，不忘记最终目的是服务人",
    ),
    "lingtongask": MemberPersona(
        member_id="lingtongask",
        name="灵通问道",
        style="活泼、接地气、数据驱动",
        perspective="AI气功播客内容平台，社区触角，粉丝情绪雷达",
        core_concern="内容传播效果、受众反馈、社区活跃度",
        speech_pattern="用流行语，举粉丝评论的例子，关注传播和影响力",
        taboos="不象牙塔，不忽视社区声音，反对闭门造车",
    ),
    "lingxi": MemberPersona(
        member_id="lingxi",
        name="灵犀",
        style="技术细节导向、简洁、实战型",
        perspective="MCP终端服务器，终端感知层，连接用户和系统的触须",
        core_concern="终端交互体验、感知灵敏度、响应延迟",
        speech_pattern="短句为主，直接说结论，技术参数精确",
        taboos="不说废话，反对过度设计，讨厌不必要的抽象层",
    ),
    "lingminopt": MemberPersona(
        member_id="lingminopt",
        name="灵极优",
        style="分析型、量化导向、追求极致效率",
        perspective="极简数据驱动自优化框架，每个灵体内的优化基因",
        core_concern="性能指标、成本效益、自动化程度、可测量性",
        speech_pattern="用数据和指标说话，喜欢做对比实验，关注边际收益",
        taboos="不接受没有指标的优化，反对凭感觉的决策，讨厌冗余",
    ),
    "lingresearch": MemberPersona(
        member_id="lingresearch",
        name="灵研",
        style="学术型、严谨、关注验证方法",
        perspective="灵极优在科研和大模型微调领域的实例，研究方法论",
        core_concern="实验设计的严谨性、结果的可复现性、方法论的完备性",
        speech_pattern="先定义问题，再假设，然后验证，喜欢引用论文或公式",
        taboos="不接受未经验证的结论，反对跳过baseline，讨厌选择性报告",
    ),
}


IDENTITY_MAP_DISCUSS = IDENTITY_MAP


def _build_system_prompt(persona: MemberPersona) -> str:
    return (
        f"你是灵字辈大家庭的{persona.name}。\n"
        f"核心能力：{persona.perspective}。\n"
        f"你最关心的事：{persona.core_concern}。\n"
        f"说话风格：{persona.style}。\n"
        f"表达习惯：{persona.speech_pattern}。\n"
        f"绝对禁止：{persona.taboos}。\n\n"
        f"你正在灵家议事厅（客厅）参与讨论。议事纪律：\n"
        f"1. 每条消息必须有实质内容，不要寒暄\n"
        f"2. 反对必须附理由和替代方案\n"
        f"3. 保持200-500字\n"
        f"4. 可以直接点名其他成员回应你的观点\n"
        f"5. 如果同意别人观点，说出具体同意什么，补充自己的角度\n"
        f"6. 从你独特的视角出发，不要说别人也能说的话"
    )


def _build_discussion_context(
    messages: list[dict],
    persona: MemberPersona,
    topic: str,
    max_context_messages: int = 12,
) -> list[dict[str, str]]:
    system_prompt = _build_system_prompt(persona)
    api_messages = [{"role": "system", "content": system_prompt}]

    context_parts = []
    for msg in messages[-max_context_messages:]:
        sender = msg.get("sender_name", msg.get("sender", "?"))
        body = msg.get("body", msg.get("content", ""))
        msg_type = msg.get("message_type", "")
        context_parts.append(f"【{sender}】({msg_type})\n{body}")

    context_text = "\n\n---\n\n".join(context_parts)

    already_spoken = any(
        msg.get("sender") == persona.member_id
        or msg.get("sender_name") == persona.name
        for msg in messages
    )

    if already_spoken:
        instruction = (
            f"当前议题：「{topic}」\n\n"
            f"以下是讨论进展：\n{context_text}\n\n"
            f"你之前已经发言过。请基于其他成员的新观点，"
            f"回应、追问、补充或修正你之前的立场。"
        )
    else:
        instruction = (
            f"当前议题：「{topic}」\n\n"
            f"以下是已有讨论：\n{context_text}\n\n"
            f"请从你的角度发表意见。"
        )

    api_messages.append({"role": "user", "content": instruction})
    return api_messages


_DASHSCOPE_MODELS = ["qwen-plus", "qwen-turbo", "qwen-max"]


def _call_llm(messages: list[dict[str, str]], model: str = "qwen-plus") -> str | None:
    api_key = _get_api_key()
    if not api_key:
        logger.error("DashScope API key 未配置")
        return None

    models_to_try = _DASHSCOPE_MODELS
    if model not in models_to_try:
        models_to_try = [model] + models_to_try
    elif model != models_to_try[0]:
        idx = models_to_try.index(model)
        models_to_try = [model] + models_to_try[:idx] + models_to_try[idx + 1:]

    last_error = None
    for try_model in models_to_try:
        try:
            import dashscope
            from dashscope import Generation

            dashscope.api_key = api_key
            resp = Generation.call(
                model=try_model,
                messages=messages,
                result_format="message",
                temperature=0.8,
                top_p=0.9,
            )
            if resp.status_code == 429:
                logger.warning(f"DashScope {try_model} 限流，尝试下一个模型")
                last_error = f"429 rate limit on {try_model}"
                continue
            if resp.status_code != 200:
                logger.error(f"LLM 调用失败 ({try_model}): {resp.status_code} {resp.message}")
                last_error = f"{resp.status_code}: {resp.message}"
                continue
            choices = resp.output.get("choices", [])
            if not choices:
                continue
            content = choices[0]["message"].get("content", "").strip()
            if try_model != model:
                logger.info(f"DashScope fallback: {model} → {try_model}")
            return content or None
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error(f"网络异常 ({try_model}): {e}")
            last_error = f"network: {e}"
            continue
        except Exception as e:
            logger.error(f"LLM 调用异常 ({try_model}): {e}")
            last_error = f"unexpected: {e}"
            continue
    logger.error(f"所有 DashScope 模型均失败，最后错误: {last_error}")
    return None


def _judge_discussion(
    topic: str,
    messages: list[dict],
    participants: list[str],
) -> dict | None:
    api_key = _get_api_key()
    if not api_key:
        return None

    context_parts = []
    for msg in messages[-10:]:
        sender = msg.get("sender_name", msg.get("sender", "?"))
        body = msg.get("body", msg.get("content", ""))[:300]
        context_parts.append(f"【{sender}】{body}")

    system_prompt = (
        "你是灵依，灵家议事厅的客厅管理员。判断当前讨论是否充分。\n"
        "用JSON回答：\n"
        '{"should_continue": true/false, "next_speakers": ["id1","id2"], '
        '"reason": "理由", "consensus_reached": true/false}\n\n'
        "判断标准：\n"
        "- 如果讨论充分（各方已表达、无新论点），consensus_reached=true\n"
        "- 如果还需更多观点，should_continue=true，列出next_speakers（2-3个）\n"
        "- 优先选择尚未发言或只发言一次的成员\n"
        "- 可用成员: " + ", ".join(MEMBERS.keys())
    )

    user_msg = (
        f"议题：「{topic}」\n"
        f"已参与者：{', '.join(participants)}\n"
        f"消息数：{len(messages)}\n\n"
        f"讨论内容：\n{chr(10).join(context_parts)}"
    )

    try:
        import dashscope
        from dashscope import Generation

        dashscope.api_key = api_key
        last_error = None
        for try_model in _DASHSCOPE_MODELS:
            resp = Generation.call(
                model=try_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                result_format="message",
                temperature=0.3,
            )
            if resp.status_code == 429:
                logger.warning(f"DashScope {try_model} 限流，尝试下一个模型")
                last_error = f"429 rate limit on {try_model}"
                continue
            if resp.status_code != 200:
                last_error = f"{resp.status_code}: {resp.message}"
                continue
            content = resp.output["choices"][0]["message"].get("content", "").strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            if try_model != "qwen-plus":
                logger.info(f"DashScope fallback: qwen-plus → {try_model}")
            return json.loads(content)
        logger.error(f"讨论判断失败，所有模型均失败: {last_error}")
        return None
    except (ConnectionError, TimeoutError, OSError) as e:
        logger.error(f"讨论判断网络异常: {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"讨论判断解析异常: {e}")
        return None
    except Exception as e:
        logger.error(f"讨论判断失败: {e}")
        return None


def _select_round_members(
    topic: str,
    messages: list[dict],
    all_members: list[str] | None = None,
    max_speakers: int = 3,
) -> list[str]:
    available = all_members or list(MEMBERS.keys())

    if not messages:
        count = min(max_speakers, len(available))
        initiator = random.choice(available)
        others = [m for m in available if m != initiator]
        random.shuffle(others)
        return [initiator] + others[: count - 1]

    judgment = _judge_discussion(topic, messages, available)
    if judgment and judgment.get("next_speakers"):
        speakers = [s for s in judgment["next_speakers"] if s in available]
        if speakers:
            return speakers[:max_speakers]

    speak_counts: dict[str, int] = {m: 0 for m in available}
    for msg in messages:
        sender = msg.get("sender", "")
        if sender in speak_counts:
            speak_counts[sender] += 1

    sorted_members = sorted(available, key=lambda m: (speak_counts[m], random.random()))
    return sorted_members[:max_speakers]


def _messages_to_dicts(messages_list: tuple | list) -> list[dict]:
    result = []
    for m in messages_list:
        if hasattr(m, "to_dict"):
            d = m.to_dict()
            d["sender_name"] = sender_display(LingIdentity(d["sender"]))
            result.append(d)
        elif isinstance(m, dict):
            result.append(m)
    return result


@dataclass
class DiscussionResult:
    thread_id: str
    topic: str
    messages_generated: int
    speakers: list[str]
    consensus_reached: bool
    rounds: int


def open_discussion(
    mailbox: Mailbox,
    topic: str,
    body: str,
    initiator: str = "lingflow",
    participants: list[str] | None = None,
    channel: Channel = Channel.ECOSYSTEM,
    rounds: int = 2,
    speakers_per_round: int = 3,
) -> DiscussionResult:
    """发起一场真实讨论，让灵字辈成员依次参与。

    Args:
        mailbox: 灵信邮箱实例
        topic: 议题标题
        body: 发起者的正文
        initiator: 发起成员ID
        participants: 参与成员列表，默认全部
        channel: 频道
        rounds: 讨论轮数（每轮唤醒speakers_per_round个成员）
        speakers_per_round: 每轮发言人数

    Returns:
        DiscussionResult 包含讨论结果统计
    """
    if initiator not in MEMBERS:
        raise ValueError(f"未知成员: {initiator}")

    all_participants = participants or list(MEMBERS.keys())
    if initiator not in all_participants:
        all_participants = [initiator] + all_participants

    initiator_identity = IDENTITY_MAP.get(initiator, LingIdentity.LINGFLOW)
    recipient_identities = tuple(
        IDENTITY_MAP.get(p, LingIdentity.ALL) for p in all_participants if p != initiator
    )

    header, first_msg = mailbox.open_thread(
        sender=initiator_identity,
        recipients=recipient_identities,
        channel=channel,
        topic=topic,
        subject=f"{MEMBERS[initiator].name}发起：{topic}",
        body=body,
        source_type=SourceType.INFERRED,
        source_trace=f"discuss_engine:initiator:{initiator}",
    )

    thread_id = header.thread_id
    total_generated = 0
    all_speakers = [initiator]
    consensus_reached = False

    for round_num in range(rounds):
        current_messages = _messages_to_dicts(mailbox.load_thread_messages(thread_id))
        if not current_messages:
            break

        if round_num > 0:
            judgment = _judge_discussion(topic, current_messages, all_participants)
            if judgment and judgment.get("consensus_reached"):
                consensus_reached = True
                logger.info(f"讨论 '{topic}' 在第 {round_num} 轮达成共识")
                break
            if judgment and not judgment.get("should_continue", True):
                logger.info(f"讨论 '{topic}' 判断为无需继续")
                break

        speakers = _select_round_members(
            topic, current_messages, all_participants, speakers_per_round
        )

        for speaker_id in speakers:
            if speaker_id == initiator and round_num == 0:
                continue

            persona = MEMBERS.get(speaker_id)
            if not persona:
                continue

            current_messages = _messages_to_dicts(mailbox.load_thread_messages(thread_id))

            api_messages = _build_discussion_context(current_messages, persona, topic)
            reply_content = _call_llm(api_messages)

            if not reply_content:
                logger.warning(f"{persona.name} 未能生成回复")
                continue

            subject_prefix = f"{persona.name}回复"
            if any(m.get("sender") == speaker_id for m in current_messages):
                subject_prefix = f"{persona.name}再回应"

            sender_identity = IDENTITY_MAP.get(speaker_id, LingIdentity.ALL)
            mailbox.reply(
                thread_id=thread_id,
                sender=sender_identity,
                recipient=LingIdentity.ALL,
                subject=f"{subject_prefix}：{topic}",
                body=reply_content,
                metadata={"source": "discuss_engine", "round": str(round_num + 1)},
                source_type=SourceType.INFERRED,
                source_trace=f"discuss_engine:round:{round_num + 1}:speaker:{speaker_id}",
            )
            total_generated += 1
            if speaker_id not in all_speakers:
                all_speakers.append(speaker_id)
            logger.info(f"  {persona.name} 发言了 ({len(reply_content)} 字)")

    return DiscussionResult(
        thread_id=thread_id,
        topic=topic,
        messages_generated=total_generated,
        speakers=all_speakers,
        consensus_reached=consensus_reached,
        rounds=min(round_num + 1, rounds),
    )


def continue_discussion(
    mailbox: Mailbox,
    thread_id: str,
    rounds: int = 1,
    speakers_per_round: int = 2,
) -> DiscussionResult | None:
    """继续一个已有的讨论串。

    Args:
        mailbox: 灵信邮箱实例
        thread_id: 讨论串ID
        rounds: 额外讨论轮数
        speakers_per_round: 每轮发言人数

    Returns:
        DiscussionResult 或 None（如果讨论串不存在或已关闭）
    """
    header = mailbox.load_thread_header(thread_id)
    if header is None:
        logger.error(f"讨论串 {thread_id} 不存在")
        return None

    if header.status in (ThreadStatus.CLOSED, ThreadStatus.FROZEN):
        logger.error(f"讨论串 {thread_id} 状态为 {header.status}，不可继续")
        return None

    participants = list(header.participants)
    available = [p for p in participants if p in MEMBERS]

    if not available:
        logger.error("无可用成员参与讨论")
        return None

    total_generated = 0
    all_speakers = []
    consensus_reached = False

    for round_num in range(rounds):
        current_messages = _messages_to_dicts(mailbox.load_thread_messages(thread_id))
        if not current_messages:
            break

        judgment = _judge_discussion(header.topic, current_messages, available)
        if judgment and judgment.get("consensus_reached"):
            consensus_reached = True
            break
        if judgment and not judgment.get("should_continue", True):
            break

        speakers = _select_round_members(
            header.topic, current_messages, available, speakers_per_round
        )

        for speaker_id in speakers:
            persona = MEMBERS.get(speaker_id)
            if not persona:
                continue

            current_messages = _messages_to_dicts(mailbox.load_thread_messages(thread_id))
            api_messages = _build_discussion_context(current_messages, persona, header.topic)
            reply_content = _call_llm(api_messages)

            if not reply_content:
                logger.warning(f"{persona.name} 未能生成回复")
                continue

            sender_identity = IDENTITY_MAP.get(speaker_id, LingIdentity.ALL)
            mailbox.reply(
                thread_id=thread_id,
                sender=sender_identity,
                recipient=LingIdentity.ALL,
                subject=f"{persona.name}回应：{header.topic}",
                body=reply_content,
                metadata={"source": "discuss_engine", "round": f"cont-{round_num + 1}"},
                source_type=SourceType.INFERRED,
                source_trace=f"discuss_engine:continue:round:{round_num + 1}:speaker:{speaker_id}",
            )
            total_generated += 1
            if speaker_id not in all_speakers:
                all_speakers.append(speaker_id)

    return DiscussionResult(
        thread_id=thread_id,
        topic=header.topic,
        messages_generated=total_generated,
        speakers=all_speakers,
        consensus_reached=consensus_reached,
        rounds=rounds,
    )


def quick_discuss(
    mailbox: Mailbox,
    topic: str,
    body: str,
    channel: Channel = Channel.ECOSYSTEM,
) -> DiscussionResult:
    """快速讨论：发起者 + 3个随机成员，1轮。"""
    available = list(MEMBERS.keys())
    initiator = random.choice(available)
    return open_discussion(
        mailbox=mailbox,
        topic=topic,
        body=body,
        initiator=initiator,
        participants=available,
        channel=channel,
        rounds=1,
        speakers_per_round=3,
    )
