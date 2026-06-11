"""灵信命令行 — 跨灵项目讨论协议的 CLI 工具"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lingmessage.adapters import (
    lingclaudeIntelAdapter,
    lingflowAdapter,
    lingyiBriefingAdapter,
)
from lingmessage.compat import import_lingyi_discussion, import_lingyi_store
from lingmessage.discuss import MEMBERS, continue_discussion, open_discussion
from lingmessage.mailbox import Mailbox
from lingmessage.store import LingBusStore, MessageStore
from typing import Union
from lingmessage.seed import seed_all
from lingmessage.governance import (
    VoteValue,
    cast_vote,
    propose,
    resolve,
    tally_votes,
)
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


def _mb(args: argparse.Namespace) -> Union[Mailbox, MessageStore]:
    backend = getattr(args, 'backend', 'mailbox')
    root = Path(args.mailbox).expanduser()
    if backend == 'lingbus':
        from lingmessage.lingbus import LingBus
        return LingBusStore(LingBus(bus_dir=root))
    return Mailbox(root=root)


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

    should_sign = getattr(args, 'sign', False)
    if should_sign:
        if not mb._get_secret_key():
            print("Error: --sign requires LINGMESSAGE_SECRET_KEY or ~/.lingmessage/.secret_key", file=sys.stderr)
            sys.exit(1)

    header, msg = mb.open_thread(
        sender=sender,
        recipients=recipients,
        channel=channel,
        topic=args.topic,
        subject=args.subject,
        body=body,
        source_type=SourceType.VERIFIED if should_sign else SourceType.INFERRED,
    )

    signed_flag = " signed=true" if should_sign else ""
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

    should_sign = getattr(args, 'sign', False)
    if should_sign:
        if not mb._get_secret_key():
            print("Error: --sign requires LINGMESSAGE_SECRET_KEY or ~/.lingmessage/.secret_key", file=sys.stderr)
            sys.exit(1)

    msg = mb.reply(
        thread_id=args.thread_id,
        sender=sender,
        recipient=recipient,
        subject=args.subject,
        body=body,
        source_type=SourceType.VERIFIED if should_sign else SourceType.INFERRED,
    )

    signed_flag = " signed=true" if should_sign else ""
    print(f"已回复 msg={msg.message_id}{signed_flag}")


def cmd_stats(args: argparse.Namespace) -> None:
    mb = _mb(args)
    if isinstance(mb, Mailbox):
        s = mb.get_summary()
        print(f"讨论串: {s['total_threads']}")
        print(f"消息总数: {s['total_messages']}")
        print(f"频道分布: {json.dumps(s['by_channel'], ensure_ascii=False)}")
        print(f"状态分布: {json.dumps(s['by_status'], ensure_ascii=False)}")
        print(f"最后更新: {s['last_updated']}")
        ds = mb.get_delivery_stats()
        print(f"送达统计: 已送达={ds['delivered']} 待送达={ds['pending']} 失败={ds['failed']} 送达率={ds['delivery_rate']:.1%}")
    else:
        s = mb.get_stats()
        print(f"统计: {json.dumps(s, ensure_ascii=False)}")


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
                if thread_dir.is_dir() and thread_dir.name not in indexed_threads and not thread_dir.name.startswith("_"):
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
    backend = getattr(args, 'backend', 'mailbox')
    if backend == 'lingbus':
        _cmd_verify_lingbus(args)
    else:
        _cmd_verify_mailbox(args)


def _cmd_verify_lingbus(args: argparse.Namespace) -> None:
    from lingmessage.lingbus import LingBus
    root = Path(args.mailbox).expanduser()
    bus = LingBus(bus_dir=root)
    signing_key = os.environ.get("LINGMESSAGE_SIGNING_KEY", "")
    total = 0
    signed_count = 0
    unsigned_count = 0
    rows = bus._conn.execute("SELECT rowid, sender, source_trace FROM messages ORDER BY rowid").fetchall()
    for r in rows:
        total += 1
        trace = r["source_trace"] or ""
        if trace.startswith("sig:"):
            signed_count += 1
            if args.verbose and signed_count <= 20:
                print(f"  SIGNED rowid={r['rowid']} sender={r['sender']}")
        else:
            unsigned_count += 1
    bus.close()
    print("=== LingBus 签名验证报告 ===")
    print(f"  总消息数: {total}")
    print(f"  已签名(sig:): {signed_count}")
    print(f"  未签名: {unsigned_count}")
    if signing_key:
        print(f"  SIGNING_KEY: 已设置({len(signing_key)}字符)")
    else:
        print("  SIGNING_KEY: 未设置")


def _cmd_verify_mailbox(args: argparse.Namespace) -> None:
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
    lf = lingflowAdapter(mb)
    n = len(lf.post_daily_reports())
    print(f"灵通日报: {n} 条")
    total += n

    lc = lingclaudeIntelAdapter(mb)
    n = len(lc.post_digests())
    print(f"灵克情报: {n} 条")
    total += n

    ly = lingyiBriefingAdapter(mb)
    n = len(ly.post_briefings())
    print(f"灵依简报: {n} 条")
    total += n

    imported = import_lingyi_store(mb)
    print(f"灵依讨论导入: {len(imported)} 个")
    total += len(imported)

    print(f"\n共同步 {total} 项")


def cmd_import(args: argparse.Namespace) -> None:
    mb = _mb(args)
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_relative_to(Path.cwd()) and not str(path).startswith(str(Path.home())) and not str(path).startswith("/tmp"):
        print("安全限制：仅允许导入当前目录、家目录或/tmp下的文件", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"文件格式错误: {e}", file=sys.stderr)
        sys.exit(1)
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


def cmd_alive(args: argparse.Namespace) -> None:
    from lingmessage.alive import check_all, format_report

    results = check_all()
    print(format_report(results, verbose=args.verbose))


def cmd_propose(args: argparse.Namespace) -> None:
    mb = _mb(args)
    proposer = LingIdentity(args.proposer)
    recipients = tuple(LingIdentity(r) for r in args.recipients.split(","))
    channel = Channel(args.channel)
    body = args.body
    if body == "-" or not body:
        body = sys.stdin.read()

    quorum = args.quorum if args.quorum else None
    deadline = args.deadline if args.deadline else None

    header, msg = propose(
        mb,
        proposer=proposer,
        recipients=recipients,
        channel=channel,
        topic=args.topic,
        body=body,
        quorum=quorum,
        deadline_hours=deadline,
    )
    print(f"提案已创建 thread={header.thread_id} proposal={msg.message_id}")


def cmd_vote(args: argparse.Namespace) -> None:
    mb = _mb(args)
    voter = LingIdentity(args.voter)
    vote = VoteValue(args.vote)
    msg = cast_vote(
        mb,
        thread_id=args.thread_id,
        voter=voter,
        vote=vote,
        reason=args.reason or "",
    )
    print(f"投票已记录 msg={msg.message_id} vote={vote.value}")


def cmd_tally(args: argparse.Namespace) -> None:
    mb = _mb(args)
    tally = tally_votes(mb, args.thread_id)
    print(f"赞成: {tally.approve}  反对: {tally.reject}  弃权: {tally.abstain}")
    for voter, vote in tally.voters.items():
        print(f"  {voter}: {vote}")


def cmd_resolve(args: argparse.Namespace) -> None:
    mb = _mb(args)
    resolver = LingIdentity(args.resolver)
    result = resolve(
        mb,
        thread_id=args.thread_id,
        resolver=resolver,
        auto=args.auto,
    )
    print(f"提案状态: {result.status.value}")
    print(f"赞成: {result.tally.approve}  反对: {result.tally.reject}  弃权: {result.tally.abstain}")
    if result.decision_message_id:
        print(f"决议消息: {result.decision_message_id}")


def cmd_hash_check(args: argparse.Namespace) -> None:
    from lingmessage.lingbus import LingBus
    from lingmessage.constraint_hash import check_and_alert, get_current_hashes

    root = Path(args.mailbox).expanduser()
    bus = LingBus(bus_dir=root)
    try:
        if args.list:
            hashes = get_current_hashes(bus, member=args.member)
            if not hashes:
                print("（无哈希记录，请先运行不带 --list 的检查建立基线）")
                return
            for h in hashes:
                print(f"  {h['member']}/{h['filename']}: {h['hash_sha256'][:16]}... @ {h['recorded_at'][:19]}")
            print(f"\n共 {len(hashes)} 条记录")
        else:
            changes = check_and_alert(bus)
            if not changes:
                print("✅ 无变更")
            else:
                new = [c for c in changes if c["change_type"] == "new"]
                modified = [c for c in changes if c["change_type"] == "modified"]
                if new:
                    print(f"ℹ️  注册 {len(new)} 个新文件")
                if modified:
                    print(f"⚠️  检测到 {len(modified)} 项变更:")
                    for c in modified:
                        print(f"  {c['member']}/{c['filename']}: {c['old_hash'][:16]}... → {c['new_hash'][:16]}...")
    finally:
        bus.close()


def cmd_sdt(args: argparse.Namespace) -> None:
    from lingmessage.lingbus import LingBus
    from lingmessage.sdt_registry import (
        SDTEntry,
        check_stale,
        get_exec_log,
        get_sdt_stats,
        list_sdts,
        register_sdt,
    )

    root = Path(args.mailbox).expanduser()
    bus = LingBus(bus_dir=root)
    try:
        if args.sdt_command == "register":
            entry = SDTEntry(
                member=args.member,
                sdt_id=args.sdt_id,
                name=args.name,
                description=args.description,
                direction=args.direction,
                priority=args.priority,
                interval_minutes=args.interval_minutes,
                risk_level=args.risk_level,
                type=args.type,
                exit_condition=args.exit_condition,
                external_verification=args.external_verification,
                sdt_version=args.sdt_version,
                verifier=args.verifier,
                status=args.status,
                enabled=args.enabled,
            )
            register_sdt(bus, entry)
            print(f"✅ SDT 已注册: {args.member}/{args.sdt_id}")

        elif args.sdt_command == "list":
            entries = list_sdts(bus, member=args.member, status=args.status)
            if not entries:
                print("（无 SDT 记录）")
                return
            print(f"{'成员':<16} {'SDT ID':<20} {'名称':<26} {'版本':<8} {'方向':<10} {'优先级':<6} {'验证方':<10} {'状态':<8}")
            print("-" * 120)
            for e in entries:
                ver = e.get("sdt_version", "") or "—"
                vf = e.get("verifier", "") or "—"
                print(f"{e['member']:<16} {e['sdt_id']:<20} {e['name']:<26} "
                      f"{ver:<8} {e['direction']:<10} {e['priority']:<6} {vf:<10} {e['status']:<8}")
            print(f"\n共 {len(entries)} 条记录")

        elif args.sdt_command == "status":
            stats = get_sdt_stats(bus, member=args.member)
            if stats["total"] == 0:
                print("（无 SDT 记录，请先注册）")
                return
            print(f"📊 SDT 健康度统计{' (成员: ' + args.member + ')' if args.member else ''}:")
            print(f"  总数: {stats['total']}  活跃: {stats['active']}  stale: {stats['stale']}")
            print(f"  已启用: {stats['enabled']}")
            print(f"  执行率: {stats['execution_rate']:.1%}")
            print(f"  成功率: {stats['success_rate']:.1%}")
            print(f"  外部验证率: {stats['external_verification_rate']:.1%}")
            print(f"  版本化率: {stats['versioned_rate']:.1%}")
            if "by_member" in stats:
                print("\n  按成员:")
                for m, ms in sorted(stats["by_member"].items()):
                    ext_rate = ms["ext_verified"] / ms["total"] if ms["total"] else 0
                    print(f"    {m:<16} 总数={ms['total']} 活跃={ms['active']} "
                          f"已执行={ms['executed']} 成功={ms['succeeded']} "
                          f"外部验证率={ext_rate:.0%}")

        elif args.sdt_command == "exec-log":
            entries = get_exec_log(bus, member=args.member, sdt_id=args.sdt_id, limit=args.limit)
            if not entries:
                print("（无执行记录）")
                return
            print(f"{'成员':<16} {'SDT ID':<20} {'执行时间':<24} {'结果':<10} {'耗时(s)':<8} {'类型':<10}")
            print("-" * 100)
            for e in entries:
                print(f"{e['member']:<16} {e['sdt_id']:<20} {e['executed_at'][:19]:<24} "
                      f"{e['result']:<10} {e['duration_s']:<8.1f} {e['log_type']:<10}")
            print(f"\n共 {len(entries)} 条记录")

        elif args.sdt_command == "check-stale":
            stale = check_stale(bus)
            if not stale:
                print("✅ 无 stale SDT")
            else:
                print(f"⚠️  发现 {len(stale)} 个 stale SDT:")
                for s in stale:
                    print(f"  {s['member']}/{s['sdt_id']} ({s['name']}) — "
                          f"{s['missed_runs']}次未执行, {s['elapsed_minutes']:.0f}m 超期")
    finally:
        bus.close()


def cmd_redzone(args: argparse.Namespace) -> None:
    from lingmessage.lingbus import LingBus
    from lingmessage.redzone import RedZoneCategory, classify_zone, require_approval

    if args.classify:
        zone = classify_zone(args.classify)
        print(f"操作分类: {zone.value}")
        return

    root = Path(args.mailbox).expanduser()
    bus = LingBus(bus_dir=root)
    try:
        category = RedZoneCategory(args.category)
        result = require_approval(
            bus,
            requester=args.requester,
            category=category,
            reason=args.reason,
            target=args.target,
            user_message=args.user_message or "",
            recipients=args.recipients.split(",") if args.recipients else None,
            quorum=args.quorum,
            deadline_hours=args.deadline,
        )
        print(f"红区审批已发起 thread={result['thread_id']}")
    finally:
        bus.close()


def cmd_pending(args: argparse.Namespace) -> None:
    from lingmessage.lingbus import LingBus

    root = Path(args.mailbox).expanduser()
    bus = LingBus(bus_dir=root)
    try:
        if args.batch_ack:
            count = bus.batch_ack(args.member)
            print(f"已批量确认 {count} 条待处理消息")
        elif args.count:
            n = bus.pending_count(args.member)
            print(f"{args.member}: {n} 条待处理消息")
        else:
            pending = bus.get_pending(args.member, limit=args.limit)
            if not pending:
                print(f"{args.member}: 无待处理消息")
                return
            for p in pending:
                print(f"  [{p['channel']}] {p['sender']} → {p['subject'] or '(无主题)'}")
                print(f"    msg={p['message_id'][:12]}...  thread={p['thread_id'][:12]}...  queued={p['queued_at'][:19]}")
            print(f"\n共 {len(pending)} 条")
    finally:
        bus.close()



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


def cmd_compress(args: argparse.Namespace) -> None:
    from lingmessage.session_compression import (
        CompressionConfig,
        CompressionLevel,
        compress_messages,
    )
    from lingmessage.session_manager import get_session_manager

    mgr = get_session_manager()
    session = mgr.load_session(args.member_id, slot_id=args.slot)
    if session is None:
        print(f"No session found for {args.member_id}")
        return

    messages = session.conversation_history
    if not messages:
        print(f"Session for {args.member_id} has no messages")
        return

    level = CompressionLevel(args.level)
    config = CompressionConfig(max_messages=args.max_messages, level=level)
    result = compress_messages(messages, config)

    if result.dropped_count == 0:
        print(f"Session has {len(messages)} messages — below threshold, no compression needed")
        return

    print(f"Compressed {result.dropped_count} messages ({level.value})")
    print(f"Archived facts: {result.archived_facts}")
    print(f"Tokens saved (est): {result.tokens_estimated_saved}")
    if result.summary_text:
        print(f"\n--- Summary ---\n{result.summary_text}")

    if args.apply:
        mgr.save_session(
            member_id=args.member_id,
            slot_id=args.slot,
            conversation_history=result.compressed_messages,
        )
        print("\nSession updated (compression applied)")
    else:
        print("\n(dry-run — use --apply to save)")

    mgr.close()


def cmd_recall(args: argparse.Namespace) -> None:
    from lingmessage.session_compression import recall_facts
    from lingmessage.session_manager import get_session_manager

    mgr = get_session_manager()
    sessions = mgr.list_active_sessions()
    if not sessions:
        print("No active sessions found")
        mgr.close()
        return

    sessions_history = []
    for s in sessions:
        mid = s["member_id"]
        if args.member and mid != args.member:
            continue
        state = mgr.load_session(mid, slot_id=s.get("slot_id", "default"))
        if state and state.conversation_history:
            sessions_history.append({
                "member_id": mid,
                "messages": state.conversation_history,
            })

    mgr.close()

    results = recall_facts(
        sessions_history,
        keyword=args.keyword,
        member_filter=args.member,
        limit=args.limit,
    )

    if not results:
        print(f"No facts matching '{args.keyword}'")
        return

    for r in results:
        print(f"[{r['member_id']}] ({r['category']}) {r['fact']}")


def cmd_session_create(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager

    mgr = FamilySessionManager()
    kwargs: dict = {"slot_id": args.slot}
    if args.key:
        kwargs["session_key"] = args.key
    if args.thread:
        kwargs["thread_id"] = args.thread

    meta = mgr.create(args.member_id, **kwargs)
    print(f"session_id={meta.session_id}")
    print(f"status={meta.status.value}")
    print(f"member={meta.member_id}")
    print(f"messages={meta.message_count}")
    mgr.close()


def cmd_session_checkpoint(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager

    mgr = FamilySessionManager()
    data: dict = {}
    if args.key:
        data["session_key"] = args.key
    if args.thread:
        data["thread_id"] = args.thread

    meta = mgr.checkpoint(args.session_id, data)
    print(f"session_id={meta.session_id}")
    print(f"status={meta.status.value}")
    print(f"messages={meta.message_count}")
    mgr.close()


def cmd_session_restore(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager

    mgr = FamilySessionManager()
    data = mgr.restore(args.session_id)
    print(f"member_id={data['member_id']}")
    print(f"slot_id={data['slot_id']}")
    print(f"session_key={data['session_key']}")
    print(f"thread_id={data['thread_id']}")
    print(f"messages={len(data['conversation_history'])}")
    print(f"adapter_state={json.dumps(data['adapter_state'], ensure_ascii=False)}")
    mgr.close()


def cmd_session_archive(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager

    mgr = FamilySessionManager()
    meta = mgr.archive(args.session_id)
    print(f"session_id={meta.session_id}")
    print(f"status={meta.status.value}")
    mgr.close()


def cmd_session_list(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager
    from lingmessage.session_protocol import SessionStatus

    mgr = FamilySessionManager()
    status = SessionStatus(args.status) if args.status else None
    sessions = mgr.list_sessions(member_id=args.member, status=status)
    if not sessions:
        print("（无会话）")
    for s in sessions:
        print(f"  {s.session_id}  status={s.status.value}  msgs={s.message_count}  size={s.size_bytes}")
    mgr.close()


def cmd_session_info(args: argparse.Namespace) -> None:
    from lingmessage.session_manager import FamilySessionManager

    mgr = FamilySessionManager()
    meta = mgr.get_metadata(args.session_id)
    print(f"session_id={meta.session_id}")
    print(f"member_id={meta.member_id}")
    print(f"status={meta.status.value}")
    print(f"created={meta.created_at}")
    print(f"updated={meta.updated_at}")
    print(f"messages={meta.message_count}")
    print(f"size_bytes={meta.size_bytes}")
    if meta.extra:
        print(f"extra={json.dumps(meta.extra, ensure_ascii=False)}")
    mgr.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lingmessage",
        description="灵信 — 灵字辈跨项目讨论协议",
    )
    parser.add_argument("--mailbox", default="~/.lingmessage", help="邮箱路径")
    parser.add_argument("--backend", choices=["mailbox", "lingbus"], default="mailbox", help="存储后端")
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

    p_alive = sub.add_parser("alive", help="检查灵族成员存活状态")
    p_alive.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    p_propose = sub.add_parser("propose", help="发起治理提案")
    p_propose.add_argument("--proposer", required=True, choices=[i.value for i in LingIdentity])
    p_propose.add_argument("--recipients", required=True, help="接收者，逗号分隔")
    p_propose.add_argument("--channel", default="governance", choices=[c.value for c in Channel])
    p_propose.add_argument("--topic", required=True, help="提案议题")
    p_propose.add_argument("--body", default="", help="提案正文，- 表示从 stdin 读取")
    p_propose.add_argument("--quorum", type=int, default=None, help="通过所需最低投票数")
    p_propose.add_argument("--deadline", type=int, default=None, help="投票截止时间（小时）")

    p_vote = sub.add_parser("vote", help="对提案投票")
    p_vote.add_argument("thread_id", help="提案线程 ID")
    p_vote.add_argument("--voter", required=True, choices=[i.value for i in LingIdentity])
    p_vote.add_argument("--vote", required=True, choices=[v.value for v in VoteValue])
    p_vote.add_argument("--reason", default="", help="投票理由")

    p_tally = sub.add_parser("tally", help="统计提案票数")
    p_tally.add_argument("thread_id", help="提案线程 ID")

    p_resolve = sub.add_parser("resolve", help="决议提案")
    p_resolve.add_argument("thread_id", help="提案线程 ID")
    p_resolve.add_argument("--resolver", required=True, choices=[i.value for i in LingIdentity])
    p_resolve.add_argument("--auto", action="store_true", help="仅在达到法定人数时决议")

    p_poll = sub.add_parser("poll", help="议事轮询守护进程")
    p_poll.add_argument("--once", dest="poll_once", action="store_true", help="单次扫描后退出")
    p_poll.add_argument("--init", dest="poll_init", action="store_true", help="初始化：标记所有现有讨论为已扫描")
    p_poll.add_argument("--interval", type=int, default=300, help="轮询间隔（秒），默认 300")

    p_compress = sub.add_parser("compress", help="压缩 session 对话历史")
    p_compress.add_argument("member_id", help="成员 ID")
    p_compress.add_argument("--slot", default="default", help="slot ID")
    p_compress.add_argument("--max-messages", type=int, default=24, help="保留最大消息数")
    p_compress.add_argument("--level", choices=["truncate", "summary", "aggressive"], default="summary")
    p_compress.add_argument("--apply", action="store_true", help="应用压缩（默认 dry-run）")

    p_recall = sub.add_parser("recall", help="搜索 session 中的事实")
    p_recall.add_argument("keyword", help="搜索关键词")
    p_recall.add_argument("--member", default=None, help="限定成员")
    p_recall.add_argument("--limit", type=int, default=5, help="最大结果数")

    p_session_create = sub.add_parser("session-create", help="创建会话")
    p_session_create.add_argument("member_id", help="成员 ID")
    p_session_create.add_argument("--slot", default="default", help="slot ID")
    p_session_create.add_argument("--key", default="", help="session key")
    p_session_create.add_argument("--thread", default="", help="thread ID")

    p_session_checkpoint = sub.add_parser("session-checkpoint", help="保存会话检查点")
    p_session_checkpoint.add_argument("session_id", help="会话 ID (member:slot)")
    p_session_checkpoint.add_argument("--key", default="", help="更新 session key")
    p_session_checkpoint.add_argument("--thread", default="", help="更新 thread ID")

    p_session_restore = sub.add_parser("session-restore", help="恢复会话状态")
    p_session_restore.add_argument("session_id", help="会话 ID (member:slot)")

    p_session_archive = sub.add_parser("session-archive", help="归档会话")
    p_session_archive.add_argument("session_id", help="会话 ID (member:slot)")

    p_session_list = sub.add_parser("session-list", help="列出会话")
    p_session_list.add_argument("--member", default=None, help="按成员过滤")
    p_session_list.add_argument("--status", default=None, choices=["active", "checkpointed", "archived", "expired"])

    p_session_info = sub.add_parser("session-info", help="会话元数据")
    p_session_info.add_argument("session_id", help="会话 ID (member:slot)")

    p_health = sub.add_parser("health", help="健康检查")
    p_health.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    p_annotate = sub.add_parser("annotate", help="历史数据标注")
    p_annotate.add_argument("--force", action="store_true", help="应用标注（默认为预览模式）")

    p_verify = sub.add_parser("verify", help="消息验证报告")
    p_verify.add_argument("thread_id", nargs="?", default=None, help="指定讨论串（默认全部）")
    p_verify.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    p_hash = sub.add_parser("hash", help="约束文件哈希校验")
    p_hash.add_argument("--list", action="store_true", help="列出已注册的哈希记录")
    p_hash.add_argument("--member", default=None, help="按成员过滤")

    p_redzone = sub.add_parser("redzone", help="红区操作审批")
    p_redzone.add_argument("--classify", metavar="OPERATION", help="分类操作到 GREEN/YELLOW/RED 区域")
    p_redzone.add_argument("--requester", default="lingmessage", help="请求者身份")
    p_redzone.add_argument("--category", default="other",
                           choices=["kill_process", "delete_data", "modify_constraint",
                                    "modify_infra", "budget_exceed", "modify_membership", "other"])
    p_redzone.add_argument("--reason", default="", help="操作理由")
    p_redzone.add_argument("--target", default="", help="操作目标")
    p_redzone.add_argument("--user-message", default="", help="用户消息")
    p_redzone.add_argument("--recipients", default=None, help="接收者（逗号分隔）")
    p_redzone.add_argument("--quorum", type=int, default=2, help="最低投票数")
    p_redzone.add_argument("--deadline", type=int, default=24, help="投票截止时间（小时）")

    p_sdt = sub.add_parser("sdt", help="SDT 注册表管理")
    sdt_sub = p_sdt.add_subparsers(dest="sdt_command")

    p_sdt_register = sdt_sub.add_parser("register", help="注册/更新 SDT")
    p_sdt_register.add_argument("--member", required=True, choices=[i.value for i in LingIdentity])
    p_sdt_register.add_argument("--sdt-id", required=True, help="SDT 标识符，如 SDT-lm-001")
    p_sdt_register.add_argument("--name", required=True, help="任务名称")
    p_sdt_register.add_argument("--description", default="", help="任务描述")
    p_sdt_register.add_argument("--direction", default="", help="所属方向")
    p_sdt_register.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], default="P2")
    p_sdt_register.add_argument("--interval-minutes", type=int, default=1440, help="执行间隔（分钟）")
    p_sdt_register.add_argument("--risk-level", choices=["low", "medium", "high", "critical"], default="low")
    p_sdt_register.add_argument("--type", choices=["delivery", "monitor", "maintenance", "learning"], default="delivery")
    p_sdt_register.add_argument("--exit-condition", default="", help="退出条件")
    p_sdt_register.add_argument("--external-verification", default="", help="外部验证方式（L1/L2）")
    p_sdt_register.add_argument("--status", choices=["active", "disabled", "retired", "stale"], default="active")
    p_sdt_register.add_argument("--enabled", action="store_true", default=True, help="是否启用")
    p_sdt_register.add_argument("--sdt-version", default="0.1.0", help="SDT 版本号")
    p_sdt_register.add_argument("--verifier", default="", help="外部验证方成员")

    p_sdt_list = sdt_sub.add_parser("list", help="列出已注册的 SDT")
    p_sdt_list.add_argument("--member", default=None, choices=[i.value for i in LingIdentity])
    p_sdt_list.add_argument("--status", default=None, choices=["active", "disabled", "retired", "stale"])

    p_sdt_log = sdt_sub.add_parser("exec-log", help="查看 SDT 执行记录")
    p_sdt_log.add_argument("--member", default=None, choices=[i.value for i in LingIdentity])
    p_sdt_log.add_argument("--sdt-id", default=None, help="SDT 标识符")
    p_sdt_log.add_argument("--limit", type=int, default=20, help="最多显示条数")

    sdt_sub.add_parser("check-stale", help="检查并标记 stale SDT")

    p_sdt_status = sdt_sub.add_parser("status", help="SDT 健康度统计")
    p_sdt_status.add_argument("--member", default=None, choices=[i.value for i in LingIdentity])

    p_pending = sub.add_parser("pending", help="按需层成员待处理消息")
    p_pending.add_argument("member", help="成员身份")
    p_pending.add_argument("--batch-ack", action="store_true", help="批量确认所有待处理消息")
    p_pending.add_argument("--count", action="store_true", help="仅显示数量")
    p_pending.add_argument("--limit", type=int, default=100, help="最多显示条数")

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
        "alive": cmd_alive,
        "propose": cmd_propose,
        "vote": cmd_vote,
        "tally": cmd_tally,
        "resolve": cmd_resolve,
        "compress": cmd_compress,
        "recall": cmd_recall,
        "session-create": cmd_session_create,
        "session-checkpoint": cmd_session_checkpoint,
        "session-restore": cmd_session_restore,
        "session-archive": cmd_session_archive,
        "session-list": cmd_session_list,
        "session-info": cmd_session_info,
        "hash": cmd_hash_check,
        "sdt": cmd_sdt,
        "redzone": cmd_redzone,
        "pending": cmd_pending,
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
