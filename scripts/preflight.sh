#!/usr/bin/env bash
#
# preflight.sh — 检查默认工具链可用性，并说明缺失时的降级路径。
#
# 只读本机环境，不发任何网络请求。
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

have() { command -v "$1" >/dev/null 2>&1; }

ver_of() {
  case "$1" in
    curl)    curl --version 2>/dev/null | head -1 | awk '{print $2}' ;;
    openssl) openssl version 2>/dev/null | awk '{print $2}' ;;
    dig)     dig -v 2>&1 | head -1 | awk '{print $NF}' ;;
    jq)      jq --version 2>/dev/null | sed 's/^jq-//' ;;
    node)    node --version 2>/dev/null ;;
    python3) python3 --version 2>/dev/null | awk '{print $2}' ;;
    *)       echo "-" ;;
  esac
}

py_mod() { python3 -c "import $1" >/dev/null 2>&1; }

printf '%-12s\t%-10s\t%-10s\t%s\n' "tool" "status" "version" "用途 / 缺失时的影响"

missing_required=()
missing_optional=()

row() {
  local tool="$1" need="$2" use="$3" status ver
  if have "$tool"; then status="ok"; ver="$(ver_of "$tool")"; else status="MISSING"; ver="-"; fi
  printf '%-12s\t%-10s\t%-10s\t%s\n' "$tool" "$status" "$ver" "$use"
  [[ "$status" == "MISSING" && "$need" == "required" ]] && missing_required+=("$tool")
  [[ "$status" == "MISSING" && "$need" != "required" ]] && missing_optional+=("$tool")
  return 0
}

row curl     required "HTTP 对照与快筛；缺失则整个 skill 无法工作"
row python3  required "内容区度量 / 报告生成；缺失则 --selector 与 report.py 不可用"
row openssl  optional "TLS 版本 / 证书 / SNI 检查；缺失可退回 curl -v"
row dig      optional "DNS 解析排查；缺失可退回 curl -v 看解析错误"
row jq       optional "响应头与 JSON 处理；缺失可用 python3 -m json.tool"
row node     optional "undici 客户端对照 / JS 语法检查；缺失则矩阵少一行"

echo
if have python3; then
  if py_mod curl_cffi; then
    printf '%-12s\t%-10s\t%-10s\t%s\n' "curl_cffi" "ok" "$(python3 -c 'import curl_cffi; print(getattr(curl_cffi, "__version__", "?"))' 2>/dev/null)" "浏览器级 TLS 指纹仿真"
  else
    printf '%-12s\t%-10s\t%-10s\t%s\n' "curl_cffi" "MISSING" "-" "TLS 指纹对照；缺失则矩阵只能对比原生栈"
    missing_optional+=("curl_cffi")
  fi
  if py_mod yaml; then
    printf '%-12s\t%-10s\t%-10s\t%s\n' "pyyaml" "ok" "$(python3 -c 'import yaml; print(yaml.__version__)' 2>/dev/null)" "report.py 解析 YAML（可选）"
  else
    printf '%-12s\t%-10s\t%-10s\t%s\n' "pyyaml" "absent" "-" "report.py 将使用内置受限解析；复杂 YAML 会报错"
  fi
fi

echo
if [[ ${#missing_required[@]} -gt 0 ]]; then
  echo "缺少必需工具：${missing_required[*]}"
  echo "  curl:     系统包管理器安装"
  echo "  python3:  系统包管理器安装"
else
  echo "必需工具齐全。"
fi

if [[ ${#missing_optional[@]} -gt 0 ]]; then
  echo "缺少可选工具：${missing_optional[*]}（对应能力会降级，不影响主流程）"
  echo "  curl_cffi: python3 -m pip install --user curl-cffi"
  echo "  node:      只有需要 undici 对照时安装（>=18）"
  echo "  openssl / dig / jq: 按上面的用途按需安装"
fi

echo
echo "下一步："
echo "  bash $ROOT/scripts/quick-check.sh <url>        # 先判可行性"
echo "  bash $ROOT/scripts/client-matrix.sh <url> --selector '#content'"
