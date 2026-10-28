#!/usr/bin/env bash
#
# install.sh — 把本 skill 链接或复制到某个 Agent 的 skills 目录。
#
# 用法: ./adapters/install.sh <skills 目录>
#   ./adapters/install.sh .claude/skills
#   ./adapters/install.sh ~/.claude/skills
#
set -euo pipefail

TARGET="${1:-}"
NAME="site-accessibility-recon"

if [[ -z "$TARGET" ]]; then
  cat <<'EOF'
用法: ./adapters/install.sh <skills 目录>

示例:
  ./adapters/install.sh .claude/skills     # 项目级
  ./adapters/install.sh ~/.claude/skills   # 用户级

先把 <skills 目录> 换成你自己的 Agent 实际读取的目录（位置随版本变化，查对应文档）。
EOF
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$TARGET"
TARGET_ABS="$(cd "$TARGET" && pwd)"
DEST="$TARGET_ABS/$NAME"

if [[ -e "$DEST" || -L "$DEST" ]]; then
  echo "目标已存在，未做改动: $DEST" >&2
  echo "如需更新，请先自行确认并移除它（脚本不会删除任何已有内容）。" >&2
  exit 1
fi

copy_skill() {
  mkdir -p "$DEST"
  cp -R "$ROOT/SKILL.md" "$DEST/"
  for d in references scripts assets; do
    [[ -d "$ROOT/$d" ]] && cp -R "$ROOT/$d" "$DEST/"
  done
}

# 目标位于仓库内部时不能用符号链接：会形成指向自身的循环，
# 导致遍历 skills 目录的工具无限递归。
case "$TARGET_ABS/" in
  "$ROOT"/*)
    copy_skill
    echo "目标在仓库内部，已复制（避免符号链接自引用）: $DEST"
    ;;
  *)
    if ln -s "$ROOT" "$DEST" 2>/dev/null; then
      echo "已链接: $DEST -> $ROOT"
    else
      copy_skill
      echo "符号链接不可用，已复制: $DEST"
      echo "注意：复制后不会随仓库更新，请自行同步。"
    fi
    ;;
esac

echo "验证：确认 $DEST/SKILL.md 存在，然后重启或刷新你的 Agent。"
