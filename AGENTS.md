# AGENTS.md - LingMessage Agent Guide

## Project Structure

lingmessage/
  __init__.py       - __version__ = 0.1.0
  types.py          - Message, ThreadHeader, LingIdentity, Channel, MessageType, ThreadStatus
  mailbox.py        - Mailbox (file-system CRUD + index)
  seed.py           - 6 seed discussions (21 messages)
  adapters.py       - LingFlowAdapter, LingClaudeIntelAdapter, LingYiBriefingAdapter
  compat.py         - LingYi lingmessage.py bidirectional conversion
  cli.py            - CLI: list, read, send, reply, stats, seed, sync, import
tests/
  test_lingmessage.py   - 21 core tests
  test_adapters.py      - 6 adapter tests
  test_compat.py        - 10 compat tests

## Commands

  python3 -m pytest tests/ -v --tb=short
  python3 -m lingmessage.cli --help
  python3 -m lingmessage.cli list
  python3 -m lingmessage.cli read <thread_id>
  python3 -m lingmessage.cli stats
  python3 -m lingmessage.cli sync
  python3 -m lingmessage.cli seed

## Key APIs

### Mailbox

  mailbox = Mailbox()  # defaults to ~/.lingmessage/
  mailbox.open_thread(sender, recipients, channel, topic, subject, body)
  mailbox.reply(thread_id, sender, recipient, subject, body)
  mailbox.list_threads(channel, status, participant)
  mailbox.load_thread_messages(thread_id)
  mailbox.get_summary()

### Adapters

  LingFlowAdapter(mailbox).post_daily_reports()
  LingClaudeIntelAdapter(mailbox).post_digests()
  LingYiBriefingAdapter(mailbox).post_briefings()

### Compat (LingYi interop)

  import_lingyi_discussion(mailbox, lingyi_dict)
  import_lingyi_store(mailbox, lingyi_root)
  export_to_lingyi_format(messages)

## Identity Map

  lingflow    = LINGFLOW (灵通)
  lingclaude  = LINGCLAUDE (灵克)
  lingyi      = LINGYI (灵依)
  lingzhi     = LINGZHI (灵知)
  lingtongask = LINGTONGASK (灵通问道)
  lingterm    = LINGXI (灵犀)
  lingxi      = LINGXI (灵犀)
  lingminopt  = LINGMINOPT (灵极优)
  lingresearch = LINGRESEARCH (灵研)
  zhibridge   = LINGZHI (智桥)

## Channels

  ecosystem    - Architecture, strategy, open source
  integration  - Inter-project API design
  shared-infra - Intelligence pipeline, capability registry
  knowledge    - Knowledge sharing, cross-domain queries
  self-optimize - Unified optimization rule base
  identity     - Brand, culture, philosophy

## Test Coverage

37 tests in 3 files:
  TestTypes (8), TestMailbox (8), TestSeed (5)
  TestLingFlowAdapter (2), TestLingClaudeIntelAdapter (2), TestLingYiBriefingAdapter (2)
  TestIdentityMapping (3), TestImportLingYiDiscussion (3), TestImportLingYiStore (2), TestExportToLingYiFormat (2)
