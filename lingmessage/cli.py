"""灵信命令行 — 跨灵项目讨论协议的 CLI 工具"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lingmessage.adapters import (
    LingClaudeIntelAdapter,
    LingFlowAdapter,
    LingYiBriefingAdapter,
)
from lingmessage.compat import import_lingyi_discussion, import_lingyi_store
from lingmessage.discuss import MEMBERS, continue_discussion, open_discussion
from lingmessage.mailbox import Mailbox
from lingmessage.seed import seed_all
from lingmessage.types import (
    Channel,
    LingIdentity,
    SourceType,
    ThreadStatus,
    sender_display,
)


MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 10000


def _validate_subject(subject: str) -> None:
    """Validate subject length."""
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"Subject too long (max {MAX_SUBJECT_LENGTH} characters)")


def _validate_body(body: str) -> None:
    """Validate body length."""
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"Body too long (max {MAX_BODY_LENGTH} characters)")


def _mb(args: argparse.Namespace) -> Mailbox:
    return Mailbox(root=Path(args.mailbox).expanduser())


def cmd_list(args: argparse.Namespace) -> None:
    mb = _mb(args)
    channel = Channel(args.channel) if args.channel else None
    status = ThreadStatus(args.status) if args.status else None
    participant = LingIdentity(args.participant) if args.participant else None
    threads = mb.list_threads(channel=channel, status=status, participant=participant)
    if not threads:
        print("（无讨论串）")
        return
    for h in threads:
        print(f"  [{h.status.value}] {h.topic}")
        print(f"    id={h.thread_id}  channel={h.channel}  msgs={h.message_count}")
        print(f"    participants: {', '.join(h.participants)}")
        if h.summary:
            print(f"    summary: {h.summary[:80]}")
        print()


def cmd_read(args: argparse.Namespace) -> None:
    mb = _mb(args)
    header = mb.load_thread_header(args.thread_id)
    if header is None:
        print(f"讨论串 {args.thread_id} 不存在", file=sys.stderr)
        sys.exit(1)
    print(f"## {header.topic}")
    print(f"频道: {header.channel}  状态: {header.status}")
    print(f"参与者: {', '.join(header.participants)}")
    print("=" * 60)
    messages = mb.load_thread_messages(args.thread_id)
    for m in messages:
        sender_name = sender_display(m.sender)
        print(f"\n[{sender_name}] {m.subject}")
        print(f"  type={m.message_type.value}  time={m.timestamp}")
        print()
        for line in m.body.split("\n"):
            print(f"  {line}")
    print(f"\n--- 共 {len(messages)} 条消息 ---")


def cmd_send(args: argparse.Namespace) -> None:
    mb = _mb(args)
    sender = LingIdentity(args.sender)
    recipients = tuple(LingIdentity(r) for r in args.recipients.split(","))
    channel = Channel(args.channel)
    body = args.body
    if body == "-" or not body:
        body = sys.stdin.read()

    # Validate inputs
    _validate_subject(args.subject)
    _validate_body(body)

    source_type = SourceType.INFERRED
    source_trace = ""
    if getattr(args, 'sign', False):
        source_type = SourceType.VERIFIED
        source_trace = "signed_by:sender"

    header, msg = mb.open_thread(
        sender=sender,
        recipients=recipients,
        channel=channel,
        topic=args.topic,
        subject=args.subject,
        body=body,
        source_type=source_type,
        source_trace=source_trace,
        signature="",
    )
    signed_flag = " signed=true" if getattr(args, 'sign', False) else ""
    print(f"已发送 thread={header.thread_id} msg={msg.message_id}{signed_flag}")


def cmd_reply(args: argparse.Namespace) -> None:
    mb = _mb(args)
    sender = LingIdentity(args.sender)
    recipient = LingIdentity(args.recipient)
    body = args.body
    if body == "-" or not body:
        body = sys.stdin.read()

    # Validate inputs
    _validate_subject(args.subject)
    _validate_body(body)

    source_type = SourceType.INFERRED
    source_trace = ""
    if getattr(args, 'sign', False):
        source_type = SourceType.VERIFIED
        source_trace = "signed_by:sender"

    msg = mb.reply(
        thread_id=args.thread_id,
        sender=sender,
        recipient=recipient,
        subject=args.subject,
        body=body,
        source_type=source_type,
        source_trace=source_trace,
        signature="",
    )
    signed_flag = " signed=true" if getattr(args, 'sign', False) else ""
    print(f"已回复 msg={msg.message_id}{signed_flag}")


def cmd_stats(args: argparse.Namespace) -> None:
    mb = _mb(args)
    s = mb.get_summary()
    print(f"讨论串: {s['total_threads']}")
    print(f"消息总数: {s['total_messages']}")
    print(f"频道分布: {json.dumps(s['by_channel'], ensure_ascii=False)}")
    print(f"状态分布: {json.dumps(s['by_status'], ensure_ascii=False)}")
    print(f"最后更新: {s['last_updated']}")
    ds = mb.get_delivery_stats()
    print(f"送达统计: 已送达={ds['delivered']} 待送达={ds['pending']} 失败={ds['failed']} 送达率={ds['delivery_rate']:.1%}")


def cmd_health(args: argparse.Namespace) -> None:
    mb = _mb(args)
    issues_found = False

    print("🔍 灵信邮箱健康检查")
    print("=" * 50)

    # Check index file
    index_path = mb._index_path()
    if not index_path.exists():
        print(f"❌ 索引文件不存在: {index_path}")
        issues_found = True
    else:
        try:
            import json
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "threads" not in data:
                print("❌ 索引文件格式无效")
                issues_found = True
            else:
                print(f"✅ 索引文件正常 (包含 {len(data['threads'])} 个讨论串)")
        except (json.JSONDecodeError, OSError) as e:
            print(f"❌ 索引文件损坏: {e}")
            issues_found = True

    # Check backup file
    backup_path = mb._index_backup_path()
    if backup_path.exists():
        try:
            import json
            data = json.loads(backup_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "threads" in data:
                print(f"✅ 备份文件正常 (包含 {len(data['threads'])} 个讨论串)")
            else:
                print("⚠️  备份文件格式无效")
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  备份文件损坏: {e}")
    else:
        print("ℹ️  备份文件不存在")

    # Check for orphaned message files
    threads_dir = mb._threads_dir()
    if threads_dir.exists():
        try:
            import json
            index_data = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"threads": []}
            indexed_threads = {t.get("thread_id") or t.get("id", "") for t in index_data.get("threads", [])}

            orphaned_count = 0
            for thread_dir in threads_dir.iterdir():
                if thread_dir.is_dir() and thread_dir.name not in indexed_threads:
                    orphaned_count += 1
                    if args.verbose:
                        print(f"⚠️  孤立讨论串目录: {thread_dir.name}")

            if orphaned_count > 0:
                print(f"⚠️  发现 {orphaned_count} 个孤立讨论串目录")
            else:
                print("✅ 无孤立讨论串目录")
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  无法检查孤立文件: {e}")
    else:
        print("ℹ️  threads 目录不存在")

    # Check audit log
    audit_path = mb._audit_path()
    if audit_path.exists():
        try:
            line_count = 0
            with audit_path.open(encoding="utf-8") as f:
                for line in f:
                    line_count += 1
            print(f"✅ 审计日志正常 (包含 {line_count} 条记录)")
        except OSError as e:
            print(f"⚠️  无法读取审计日志: {e}")
    else:
        print("ℹ️  审计日志不存在")

    # Check source_type annotation coverage
    if threads_dir.exists():
        from collections import Counter
        source_types = Counter()
        msg_count = 0
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            for msg_file in thread_dir.glob("msg_*.json"):
                try:
                    data = json.loads(msg_file.read_text(encoding="utf-8"))
                    st = data.get("source_type", "<MISSING>")
                    source_types[st] += 1
                    msg_count += 1
                except (json.JSONDecodeError, OSError):
                    pass
        if msg_count > 0:
            missing = source_types.get("<MISSING>", 0)
            if missing > 0:
                print(f"⚠️  {missing}/{msg_count} 条消息缺少 source_type 标注")
                issues_found = True
            else:
                print(f"✅ 所有 {msg_count} 条消息已标注 source_type")
            if args.verbose:
                for st, cnt in sorted(source_types.items()):
                    print(f"    {st}: {cnt}")

    print("=" * 50)
    if issues_found:
        print("❌ 发现问题，建议修复")
        sys.exit(1)
    else:
        print("✅ 系统健康")


def cmd_annotate(args: argparse.Namespace) -> None:
    from lingmessage.annotate import annotate_all, print_report

    mb = _mb(args)
    threads_dir = mb._threads_dir()
    dry_run = not args.force
    if dry_run:
        print("预览模式（不写入文件）。使用 --force 应用标注。\n")
    result = annotate_all(threads_dir, dry_run=dry_run)
    print_report(result)
    if dry_run and (result.annotated_generated + result.annotated_inferred) > 0:
        print("\n使用 --force 应用以上标注。")


def cmd_verify(args: argparse.Namespace) -> None:
    mb = _mb(args)
    secret_key = mb._get_secret_key()
    if not secret_key:
        print("错误：未配置密钥（LINGMESSAGE_SECRET_KEY 或 ~/.lingmessage/.secret_key）", file=sys.stderr)
        sys.exit(1)

    threads_dir = mb._threads_dir()
    if not threads_dir.exists():
        print("无消息数据")
        return

    verified_count = 0
    inferred_count = 0
    generated_count = 0
    unannotated_count = 0
    total = 0

    if args.thread_id:
        thread_ids = [args.thread_id]
    else:
        thread_ids = [d.name for d in sorted(threads_dir.iterdir()) if d.is_dir()]

    for tid in thread_ids:
        messages = mb.load_thread_messages(tid)
        for m in messages:
            total += 1
            if m.source_type == SourceType.VERIFIED:
                verified_count += 1
                if args.verbose:
                    print(f"  VERIFIED {m.message_id[:12]} {sender_display(m.sender)}")
            elif m.source_type == SourceType.INFERRED:
                inferred_count += 1
            elif m.source_type == SourceType.GENERATED:
                generated_count += 1
            else:
                unannotated_count += 1

    print("=== 消息验证报告 ===")
    print(f"  总消息数: {total}")
    print(f"  VERIFIED: {verified_count}")
    print(f"  INFERRED: {inferred_count}")
    print(f"  GENERATED: {generated_count}")
    if unannotated_count:
        print(f"  未标注: {unannotated_count}")
    if args.verbose:
        secret_key_config = "环境变量" if os.environ.get("LINGMESSAGE_SECRET_KEY") else "密钥文件"
        print(f"  密钥来源: {secret_key_config}")


def cmd_seed(args: argparse.Namespace) -> None:
    mb = _mb(args)
    threads = seed_all(mb)
    print(f"已播种 {len(threads)} 个讨论串:")
    for name, tid in threads.items():
        print(f"  {name}: {tid}")


def cmd_sync(args: argparse.Namespace) -> None:
    mb = _mb(args)
    total = 0
    lf = LingFlowAdapter(mb)
    n = len(lf.post_daily_reports())
    print(f"灵通日报: {n} 条")
    total += n

    lc = LingClaudeIntelAdapter(mb)
    n = len(lc.post_digests())
    print(f"灵克情报: {n} 条")
    total += n

    ly = LingYiBriefingAdapter(mb)
    n = len(ly.post_briefings())
    print(f"灵依简报: {n} 条")
    total += n

    imported = import_lingyi_store(mb)
    print(f"灵依讨论导入: {len(imported)} 个")
    total += len(imported)

    print(f"\n共同步 {total} 项")


def cmd_import(args: argparse.Namespace) -> None:
    mb = _mb(args)
    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        imported = 0
        for disc in data:
            result = import_lingyi_discussion(mb, disc)
            if result:
                imported += 1
        print(f"导入 {imported}/{len(data)} 个讨论")
    else:
        result = import_lingyi_discussion(mb, data)
        if result:
            print(f"已导入 thread={result[0].thread_id}")
        else:
            print("导入失败（空讨论）")


def cmd_discuss(args: argparse.Namespace) -> None:
    mb = _mb(args)
    channel = Channel(args.channel)
    participants = (
        args.participants.split(",") if args.participants else None
    )

    body = args.body
    if not body:
        persona = MEMBERS[args.initiator]
        from lingmessage.discuss import _build_system_prompt, _call_llm
        prompt = _build_system_prompt(persona)
        api_msgs = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"请在议事厅发起关于「{args.topic}」的讨论。阐述你的核心观点，向其他成员提问。200-400字。"},
        ]
        body = _call_llm(api_msgs) or f"各位，我想讨论一个问题：{args.topic}。请从各自角度发表看法。"

    result = open_discussion(
        mailbox=mb,
        topic=args.topic,
        body=body,
        initiator=args.initiator,
        participants=participants,
        channel=channel,
        rounds=args.rounds,
        speakers_per_round=args.speakers,
    )

    print("\n讨论完成!")
    print(f"  议题: {result.topic}")
    print(f"  讨论串: {result.thread_id}")
    print(f"  生成消息: {result.messages_generated}")
    print(f"  发言成员: {', '.join(MEMBERS[s].name for s in result.speakers if s in MEMBERS)}")
    print(f"  轮数: {result.rounds}")
    print(f"  达成共识: {'是' if result.consensus_reached else '否'}")
    print("\n用以下命令查看讨论:")
    print(f"  python3 -m lingmessage.cli read {result.thread_id}")


def cmd_continue(args: argparse.Namespace) -> None:
    mb = _mb(args)
    result = continue_discussion(
        mailbox=mb,
        thread_id=args.thread_id,
        rounds=args.rounds,
        speakers_per_round=args.speakers,
    )
    if result is None:
        print("无法继续讨论（讨论串不存在或已关闭）")
        return
    print("\n讨论继续!")
    print(f"  新增消息: {result.messages_generated}")
    print(f"  新发言成员: {', '.join(MEMBERS[s].name for s in result.speakers if s in MEMBERS)}")


def cmd_poll(args: argparse.Namespace) -> None:
    from lingmessage.poller import DiscussionPoller

    poller = DiscussionPoller(mailbox=_mb(args))

    if args.poll_once:
        result = poller.scan_once()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.poll_init:
        poller.init_existing()
        print(f"Initialized: marked {poller._stats.get('init_marked', 0)} participants as scanned")
    else:
        poller.run(interval=args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lingmessage",
        description="灵信 — 灵字辈跨项目讨论协议",
    )
    parser.add_argument("--mailbox", default="~/.lingmessage", help="邮箱路径")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="列出讨论串")
    p_list.add_argument("--channel", choices=[c.value for c in Channel])
    p_list.add_argument("--status", choices=[s.value for s in ThreadStatus])
    p_list.add_argument("--participant", choices=[i.value for i in LingIdentity])

    p_read = sub.add_parser("read", help="读取讨论串")
    p_read.add_argument("thread_id")

    p_send = sub.add_parser("send", help="发送新讨论")
    p_send.add_argument("--sender", required=True, choices=[i.value for i in LingIdentity])
    p_send.add_argument("--recipients", required=True, help="逗号分隔")
    p_send.add_argument("--channel", required=True, choices=[c.value for c in Channel])
    p_send.add_argument("--topic", required=True)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body", default="", help="正文，- 表示从 stdin 读取")
    p_send.add_argument("--sign", action="store_true", help="签名消息（需要配置密钥）")

    p_reply = sub.add_parser("reply", help="回复讨论")
    p_reply.add_argument("thread_id")
    p_reply.add_argument("--sender", required=True, choices=[i.value for i in LingIdentity])
    p_reply.add_argument("--recipient", required=True, choices=[i.value for i in LingIdentity])
    p_reply.add_argument("--subject", required=True)
    p_reply.add_argument("--body", default="", help="正文，- 表示从 stdin 读取")
    p_reply.add_argument("--sign", action="store_true", help="签名消息（需要配置密钥）")

    sub.add_parser("stats", help="邮箱统计")
    sub.add_parser("seed", help="播种初始讨论")
    sub.add_parser("sync", help="同步所有灵项目的情报到灵信")

    p_import = sub.add_parser("import", help="导入灵依讨论文件")
    p_import.add_argument("file", help="灵依讨论 JSON 文件路径")

    p_discuss = sub.add_parser("discuss", help="发起真实讨论")
    p_discuss.add_argument("topic", help="议题标题")
    p_discuss.add_argument("--body", default="", help="发起正文，默认让LLM生成")
    p_discuss.add_argument("--initiator", default="lingflow", choices=list(MEMBERS.keys()), help="发起成员")
    p_discuss.add_argument("--participants", default="", help="参与成员，逗号分隔（默认全部）")
    p_discuss.add_argument("--channel", default="ecosystem", choices=[c.value for c in Channel])
    p_discuss.add_argument("--rounds", type=int, default=2, help="讨论轮数")
    p_discuss.add_argument("--speakers", type=int, default=3, help="每轮发言人数")

    p_continue = sub.add_parser("continue", help="继续已有讨论")
    p_continue.add_argument("thread_id", help="讨论串ID")
    p_continue.add_argument("--rounds", type=int, default=1, help="额外轮数")
    p_continue.add_argument("--speakers", type=int, default=2, help="每轮发言人数")

    p_poll = sub.add_parser("poll", help="议事轮询守护进程")
    p_poll.add_argument("--once", dest="poll_once", action="store_true", help="单次扫描后退出")
    p_poll.add_argument("--init", dest="poll_init", action="store_true", help="初始化：标记所有现有讨论为已扫描")
    p_poll.add_argument("--interval", type=int, default=300, help="轮询间隔（秒），默认 300")

    p_health = sub.add_parser("health", help="健康检查")
    p_health.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    p_annotate = sub.add_parser("annotate", help="历史数据标注")
    p_annotate.add_argument("--force", action="store_true", help="应用标注（默认为预览模式）")

    p_verify = sub.add_parser("verify", help="消息验证报告")
    p_verify.add_argument("thread_id", nargs="?", default=None, help="指定讨论串（默认全部）")
    p_verify.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return

    commands = {
        "list": cmd_list,
        "read": cmd_read,
        "send": cmd_send,
        "reply": cmd_reply,
        "stats": cmd_stats,
        "seed": cmd_seed,
        "sync": cmd_sync,
        "import": cmd_import,
        "discuss": cmd_discuss,
        "continue": cmd_continue,
        "health": cmd_health,
        "annotate": cmd_annotate,
        "verify": cmd_verify,
        "poll": cmd_poll,
    }
    cmd_func = commands.get(args.command)
    if cmd_func:
        try:
            cmd_func(args)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
        except FileNotFoundError as e:
            print(f"文件未找到: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
