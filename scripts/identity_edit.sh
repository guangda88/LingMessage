#!/usr/bin/env bash
# 身份文件编辑辅助 — 解锁/加锁 CRUSH.md + AGENTS.md
# 用法:
#   ./scripts/identity_edit.sh unlock   — 解锁，允许编辑
#   ./scripts/identity_edit.sh lock     — 重新锁定（编辑完成后必须执行）
#   ./scripts/identity_edit.sh status   — 查看当前权限状态

set -euo pipefail
cd "$(dirname "$0")/.."

FILES="CRUSH.md AGENTS.md"

case "${1:-status}" in
  unlock)
    chmod u+w $FILES
    echo "🔓 已解锁: $FILES"
    ls -la $FILES | awk '{print $1, $NF}'
    ;;
  lock)
    chmod 444 $FILES
    echo "🔒 已锁定: $FILES"
    ls -la $FILES | awk '{print $1, $NF}'
    ;;
  status)
    echo "📋 身份文件权限:"
    ls -la $FILES | awk '{print $1, $NF}'
    for f in $FILES; do
      if [ -w "$f" ]; then
        echo "  ⚠️  $f 可写 — 记得编辑后执行 identity_edit.sh lock"
      else
        echo "  ✅ $f 只读"
      fi
    done
    ;;
  *)
    echo "用法: $0 {unlock|lock|status}"
    exit 1
    ;;
esac
