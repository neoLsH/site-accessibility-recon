#!/usr/bin/env bash
# quick-check.sh — 兼容薄壳：参数与退出码原样透传给 quick.py。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/quick.py" "$@"
