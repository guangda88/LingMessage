from __future__ import annotations

"""灵信命令行 — 跨灵项目讨论协议的 CLI 工具"""

import argparse
import json
import sys
from pathlib import Path

from lingmessage.adapters import (
    LingClaudeIntelAdapter,
    LingFlowAdapter,
    LingYiBriefingAdapter,
)
from lingmessage.compat import import_lingyi_discussion, import_lingyi_store
from lingmessage.mailbox import Mailbox
from lingmessage.seed import seed_all
from lingmessage.types import (
    Channel,
    LingIdentity,
    MessageType,
    ThreadStatus,
)


def _mb(args: argparse.Namespace) -> Mailbox:
    return Mailbox(root=Path(args.mailbox))


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
        sender_name = _sender_display(m.sender)
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
    header, msg = mb.open_thread(
        sender=sender,
        recipients=recipients,
        channel=channel,
        topic=args.topic,
        subject=args.subject,
        body=body,
    )
    print(f"已发送 thread={header.thread_id} msg={msg.message_id}")


def cmd_reply(args: argparse.Namespace) -> None:
    mb = _mb(args)
    sender = LingIdentity(args.sender)
    recipient = LingIdentity(args.recipient)
    body = args.body
    if body == "-" or not body:
        body = sys.stdin.read()
    msg = mb.reply(
        thread_id=args.thread_id,
        sender=sender,
        recipient=recipient,
        subject=args.subject,
        body=body,
    )
    print(f"已回复 msg={msg.message_id}")


def cmd_stats(args: argparse.Namespace) -> None:
    mb = _mb(args)
    s = mb.get_summary()
    print(f"讨论串: {s['total_threads']}")
    print(f"消息总数: {s['total_messages']}")
    print(f"频道分布: {json.dumps(s['by_channel'], ensure_ascii=False)}")
    print(f"状态分布: {json.dumps(s['by_status'], ensure_ascii=False)}")
    print(f"最后更新: {s['last_updated']}")


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


def _sender_display(identity: LingIdentity) -> str:
    names = {
        LingIdentity.LINGFLOW: "灵通",
        LingIdentity.LINGCLAUDE: "灵克",
        LingIdentity.LINGYI: "灵依",
        LingIdentity.LINGZHI: "灵知",
        LingIdentity.LINGTONGASK: "灵通问道",
        LingIdentity.LINGXI: "灵犀",
        LingIdentity.LINGMINOPT: "灵极优",
        LingIdentity.LINGRESEARCH: "灵研",
        LingIdentity.ALL: "所有人",
    }
    return names.get(identity, identity.value)


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

    p_reply = sub.add_parser("reply", help="回复讨论")
    p_reply.add_argument("thread_id")
    p_reply.add_argument("--sender", required=True, choices=[i.value for i in LingIdentity])
    p_reply.add_argument("--recipient", required=True, choices=[i.value for i in LingIdentity])
    p_reply.add_argument("--subject", required=True)
    p_reply.add_argument("--body", default="", help="正文，- 表示从 stdin 读取")

    sub.add_parser("stats", help="邮箱统计")
    sub.add_parser("seed", help="播种初始讨论")
    sub.add_parser("sync", help="同步所有灵项目的情报到灵信")

    p_import = sub.add_parser("import", help="导入灵依讨论文件")
    p_import.add_argument("file", help="灵依讨论 JSON 文件路径")

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
    }
    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)


if __name__ == "__main__":
    main()
