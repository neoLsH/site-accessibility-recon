#!/usr/bin/env bash
#
# quick-check.sh — 快速判断一批 URL 的「可访问性路径可行性」。
#
# 每个 URL 只发 1 个页面请求（外加每个 origin 1 次 robots.txt），给出可行性判定，
# 用来在开深度 recon 之前先决定「哪条路值得走、哪条路直接放弃」。
#
# 判定词表（固定，便于脚本消费）：
#   可行·HTTP      纯 HTTP 客户端就能拿到内容区
#   可行·浏览器    静态 HTML 是空壳，需要浏览器执行 JS（尚未实测，属推断）
#   疑似拦截       状态码/挑战页/极短响应体等反爬迹象
#   需授权         401 或跳转到登录页
#   不可达         DNS/连接/TLS/超时失败（L1）
#   内容不存在     404/410（软 404 需按 triage-and-gates.md 复核）
#   合规禁止       robots.txt 不允许
#   待定           信号冲突或信息不足，需要更多样本
#
# 依赖：curl、python3（量内容区 + 解析 robots）。无第三方包。
#
# 注意：
#   - 只测你有权测的目标；默认执行 robots.txt 检查，--skip-robots 仅用于
#     你已获得站点方明确许可的场景。
#   - 1 次请求不足以判定概率门；要看翻转率请用 client-matrix.sh --passes 3。
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRIX="$SCRIPT_DIR/client-matrix.sh"

DELAY=5
TIMEOUT=20
SELECTOR=""
MIN_CONTENT=200
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
SKIP_ROBOTS=0
FROM_FILE=""
URLS=()

usage() {
  cat <<'EOF'
用法: quick-check.sh <url> [<url> ...] [选项]
      quick-check.sh --from urls.txt [选项]

  --from <file>       从文件读 URL，一行一个，# 开头为注释
  --selector <css>    内容区选择器（#id / .class / 标签名）；不给则只看状态码与字节数
  --min-content <n>   内容区字符数下限，默认 200；低于此值不算「拿到内容」
  --delay <sec>       URL 之间的间隔，默认 5
  --timeout <sec>     单请求超时，默认 20
  --ua <string>       统一 User-Agent
  --skip-robots       跳过 robots.txt 检查（仅在你已获得明确许可时使用）
  -h, --help          显示帮助

输出: verdict / http / bytes / content / final_url / url / reason（Tab 分隔）
退出码: 0 = 全部可行；1 = 存在非可行项；2 = 用法错误
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)        FROM_FILE="${2:-}"; shift 2 ;;
    --selector)    SELECTOR="${2:-}"; shift 2 ;;
    --min-content) MIN_CONTENT="${2:-200}"; shift 2 ;;
    --delay)       DELAY="${2:-5}"; shift 2 ;;
    --timeout)     TIMEOUT="${2:-20}"; shift 2 ;;
    --ua)          UA="${2:-}"; shift 2 ;;
    --skip-robots) SKIP_ROBOTS=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    -*)            echo "未知选项: $1" >&2; usage >&2; exit 2 ;;
    *)             URLS+=("$1"); shift ;;
  esac
done

if [[ -n "$FROM_FILE" ]]; then
  [[ -f "$FROM_FILE" ]] || { echo "找不到文件: $FROM_FILE" >&2; exit 2; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] && URLS+=("$line")
  done < "$FROM_FILE"
fi

if [[ ${#URLS[@]} -eq 0 ]]; then
  usage >&2
  exit 2
fi

# 没写 scheme 的补 https://，否则 origin/robots 推断会失败
for i in "${!URLS[@]}"; do
  [[ "${URLS[$i]}" =~ ^https?:// ]] || URLS[$i]="https://${URLS[$i]}"
done
command -v curl >/dev/null 2>&1 || { echo "需要 curl" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "需要 python3" >&2; exit 2; }
[[ -x "$MATRIX" ]] || { echo "找不到可执行的 client-matrix.sh: $MATRIX" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BODY="$TMP/body"

# 通用拦截/挑战页特征词：只用不绑定具体厂商的措辞
MARKERS='captcha|challenge|verify you are human|are you a robot|unusual traffic|access denied|access restricted|request blocked|人机验证|安全验证|访问验证|滑动验证|环境异常|异常访问'

origin_of() {
  local u="$1"
  if [[ "$u" =~ ^(https?://[^/]+) ]]; then printf '%s' "${BASH_REMATCH[1]}"; else printf ''; fi
}

path_of() {
  local u="$1" origin
  origin="$(origin_of "$u")"
  [[ -z "$origin" ]] && { printf '/'; return; }
  local p="${u#"$origin"}"
  p="${p%%#*}"
  [[ -z "$p" || "$p" != /* ]] && p="/$p"
  printf '%s' "$p"
}

# 返回 0=允许，1=禁止，3=未知
robots_check() {
  local url="$1" origin path san code file rcode verdict
  origin="$(origin_of "$url")"
  path="$(path_of "$url")"
  [[ -z "$origin" ]] && return 3

  san="$(printf '%s' "$origin" | tr -c 'A-Za-z0-9' '_')"
  code="$(cat "$TMP/robots-$san.code" 2>/dev/null || true)"
  file="$TMP/robots-$san.txt"
  if [[ -z "$code" ]]; then
    code="$(curl -sS -L --max-time 10 -A "$UA" -o "$file" \
            -w '%{http_code}' "$origin/robots.txt" 2>/dev/null)" || code="000"
    printf '%s' "$code" > "$TMP/robots-$san.code"
  fi

  case "$code" in
    2*) ;;                  # 有 robots.txt，按内容判定
    401|403) return 1 ;;    # 标准语义：整个站点不允许抓取
    000|5*) return 3 ;;     # 取不到，视为未知
    *) return 0 ;;          # 404 等：没有限制
  esac

  rcode="$(python3 - "$file" "$UA" "$path" <<'PY' 2>/dev/null || echo unknown
import sys
from urllib.robotparser import RobotFileParser

try:
    with open(sys.argv[1], encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
except OSError:
    print('unknown')
    raise SystemExit
rp = RobotFileParser()
rp.parse(lines)
print('allow' if rp.can_fetch(sys.argv[2], sys.argv[3]) else 'deny')
PY
)"
  case "$rcode" in
    deny) return 1 ;;
    allow) return 0 ;;
    *) return 3 ;;
  esac
}

content_len() {
  [[ -z "$SELECTOR" ]] && { printf '%s' '-'; return; }
  "${MATRIX}" --html "$1" --selector "$SELECTOR" 2>/dev/null | awk -F'\t' '$1=="content"{print $2}'
}

# 输出 "verdict<TAB>reason"
classify() {
  local url="$1" http="$2" bytes="$3" ctype="$4" rc="$5" clen="$6" final="$7"

  # L1：压根没到源站
  if [[ "$rc" -ne 0 || "$http" == "000" ]]; then
    case "$rc" in
      6)  printf '不可达\tDNS 解析失败' ;;
      7)  printf '不可达\t连接失败（出口/端口不可达）' ;;
      28) printf '不可达\t超时' ;;
      35|60) printf '不可达\tTLS 握手失败（证书或栈不兼容）' ;;
      *)  printf '不可达\tcurl 退出码 %s' "$rc" ;;
    esac
    return
  fi

  local challenge=0 login=0 html=0 script=0
  grep -a -i -q -E "$MARKERS" "$BODY" 2>/dev/null && challenge=1
  grep -a -i -q -E "type=[\"']?password" "$BODY" 2>/dev/null && login=1
  [[ "$ctype" == *html* || -z "$ctype" ]] && html=1
  grep -a -i -q '<script' "$BODY" 2>/dev/null && script=1

  case "$http" in
    401|407)
      printf '需授权\tHTTP %s：需要凭证' "$http" ;;
    403|429|451|503)
      if [[ "$challenge" -eq 1 ]]; then
        printf '疑似拦截\tHTTP %s 且响应体含挑战页特征' "$http"
      else
        printf '疑似拦截\tHTTP %s（无挑战页特征，需按分诊复核）' "$http"
      fi ;;
    404|410)
      printf '内容不存在\tHTTP %s（软 404 需对照同源静态资源复核）' "$http" ;;
    2*)
      if [[ "$final" =~ /(login|signin|sign-in|auth|passport|sso)([/?#]|$) ]]; then
        printf '需授权\t重定向到登录页'
        return
      fi
      if [[ "$html" -eq 0 ]]; then
        if [[ "$bytes" -gt 0 ]]; then
          printf '可行·HTTP\t非 HTML（%s），%s 字节' "${ctype:-未知类型}" "$bytes"
        else
          printf '待定\tHTTP %s 但响应体为空' "$http"
        fi
        return
      fi
      if [[ -n "$SELECTOR" && "$clen" == "?unsupported" ]]; then
        printf '待定\t选择器过于复杂，改用简单选择器或用真实浏览器取值'
        return
      fi
      if [[ -n "$SELECTOR" && "$clen" =~ ^[0-9]+$ && "$clen" -ge "$MIN_CONTENT" ]]; then
        printf '可行·HTTP\t内容区 %s 字符（下限 %s）' "$clen" "$MIN_CONTENT"
        return
      fi
      if [[ -z "$SELECTOR" && "$bytes" -ge $((MIN_CONTENT * 4)) ]]; then
        printf '可行·HTTP\tHTML %s 字节（未量内容区，给 --selector 更准）' "$bytes"
        return
      fi

      # 走到这里：HTML 有响应，但没拿到足量内容区
      if [[ "$login" -eq 1 ]]; then
        printf '需授权\t响应体含登录表单'
        return
      fi
      if [[ "$challenge" -eq 1 ]]; then
        printf '疑似拦截\t响应体含挑战页特征'
        return
      fi
      if [[ "$script" -eq 1 && "$bytes" -ge 1024 ]]; then
        printf '可行·浏览器\t静态 HTML 无正文但有脚本（CSR 空壳，需实测浏览器）'
        return
      fi
      if [[ -z "$SELECTOR" ]]; then
        printf '待定\tHTML %s 字节，低于下限 %s（给 --selector 更准）' "$bytes" "$((MIN_CONTENT * 4))"
        return
      fi
      case "$clen" in
        missing) printf '待定\t选择器未命中，且无 CSR 迹象（路由/结构可能变了）' ;;
        0) printf '待定\t内容区为空且无 CSR 迹象（降级页或抽取失败）' ;;
        *) printf '待定\t内容区 %s 低于下限 %s，且无 CSR 迹象' "${clen:-未量}" "$MIN_CONTENT" ;;
      esac ;;
    3*)
      if [[ "$final" =~ /(login|signin|sign-in|auth|passport|sso) ]]; then
        printf '需授权\t重定向到登录页'
      else
        printf '待定\tHTTP %s 未跟随到终态' "$http"
      fi ;;
    *)
      printf '待定\tHTTP %s' "$http" ;;
  esac
}

probe() {
  local url="$1" out code bytes final ctype rc clen verdict reason rcode
  if [[ "$SKIP_ROBOTS" -eq 0 ]]; then
    robots_check "$url"
    rcode=$?
    if [[ "$rcode" -eq 1 ]]; then
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "合规禁止" "-" "-" "-" "-" "$url" "robots.txt 不允许抓取"
      return 1
    elif [[ "$rcode" -eq 3 ]]; then
      echo "# 警告：$url 的 robots.txt 取不到，合规状态未知，继续探测" >&2
    fi
  fi

  : > "$BODY"
  out="$(curl -sS -L --max-time "$TIMEOUT" -A "$UA" -o "$BODY" \
        -w '%{http_code}\t%{size_download}\t%{url_effective}\t%{content_type}' "$url" 2>/dev/null)"
  rc=$?
  IFS=$'\t' read -r code bytes final ctype <<<"$out"
  [[ "$rc" -ne 0 ]] && { code="000"; bytes=0; final="$url"; ctype=""; }

  # 非 HTML（JSON/图片等）量内容区没有意义
  if [[ "$ctype" == *html* || -z "$ctype" ]]; then
    clen="$(content_len "$BODY")"
  else
    clen="-"
  fi
  IFS=$'\t' read -r verdict reason <<<"$(classify "$url" "$code" "$bytes" "$ctype" "$rc" "$clen" "$final")"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$verdict" "$code" "$bytes" "${clen:--}" "$final" "$url" "$reason"

  case "$verdict" in
    可行*) return 0 ;;
    *) return 1 ;;
  esac
}

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "verdict" "http" "bytes" "content" "final_url" "url" "reason"
[[ "$SKIP_ROBOTS" -eq 1 ]] && echo "# 已跳过 robots.txt 检查（请确认你有该站点的明确许可）" >&2

bad=0
total=0
for url in "${URLS[@]}"; do
  [[ "$total" -gt 0 ]] && sleep "$DELAY"
  probe "$url" || bad=$((bad + 1))
  total=$((total + 1))
done

cat <<'EOF'

判定词表：
  可行·HTTP    纯 HTTP 即可拿到内容区        → 走 http-document，最省
  可行·浏览器  静态是空壳、需 JS 渲染（推断）  → 需浏览器内核，先实测再定
  疑似拦截     状态码或挑战页有反爬迹象      → 按 triage-and-gates.md 做单变量隔离
  需授权       401 / 登录页                  → 走合法鉴权（S7），不绕过
  不可达       L1 链路失败                   → 先修链路/出口，别急着看指纹
  内容不存在   404/410                       → 换数据源（S0）或核对路由
  合规禁止     robots.txt 不允许             → 停；取得许可再测
  待定         信号冲突/样本不足             → 加 --selector 或多 pass 重测
EOF

if [[ "$bad" -eq 0 ]]; then
  echo "结论：${total}/${total} 条可行"
  exit 0
fi
echo "结论：$((total - bad))/${total} 条可行，${bad} 条需进一步分析"
exit 1
