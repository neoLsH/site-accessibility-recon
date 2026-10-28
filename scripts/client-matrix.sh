#!/usr/bin/env bash
#
# client-matrix.sh — 同一 URL 跑多种客户端栈，输出状态码 / 字节数 / 内容区字符数 / 耗时 / 最终 URL。
#
# 用途：
#   1) 隔离 L2 传输层（TLS/H2 指纹）与 L3 身份层（UA）的影响。
#   2) 用内容区长度区分「降级外壳 200」与「真有正文」，并可留存原始 HTML 供复核。
#
# 依赖：curl 必需；python3 用于量内容区；node、curl_cffi、requests 按需。
#
# 注意：
#   - 只测你有权测的目标。
#   - 连续探测本身可能触发按 IP 的短窗限速，把结论污染成「被反爬」。
#     默认 5s 间隔；已知目标有短窗限速时，先用 --clients 只打 2 发。
#   - 只替换 User-Agent，不模拟 Sec-Ch-Ua / Accept-Language 等次级头；
#     要验证完整头一致性，用 curl 显式补齐后另跑一组。
#   - --selector 只支持 #id / .class / 标签名 / 它们的简单组合。
#     复合选择器（后代、属性、伪类）会输出 ?unsupported，请先用真实浏览器取值。
#   - 输出是**近似**可见字符数：仅用于跨客户端相对比较；
#     精确完整性判定以真实浏览器基线为准，见 references/metrics.md。
#   - --tls-echo 会把当前出口的指纹信息发给第三方回显服务，按需使用。
#
set -uo pipefail

URL=""
DELAY=5
TIMEOUT=20
PASSES=1
SELECTOR=""
CLIENTS="curl,cffi,node,py"
SAVE_DIR=""
HTML_FILE=""
TLS_ECHO=0

UA_CHROME='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
UA_SAFARI='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15'

usage() {
  cat <<'EOF'
用法: client-matrix.sh <url> [选项]
      client-matrix.sh --html <file> --selector <css>   # 离线量已有 HTML

  --selector <css>   量内容区（#id / .class / 标签名），默认不量
  --passes <n>       重复跑几轮，默认 1；疑似概率门用 3
  --clients <list>   只跑指定客户端：curl,cffi,node,py，默认全部
                     （curl 与 cffi 各跑 chrome/safari 两个 UA，共 2 发）
  --save-html <dir>  把每个客户端的响应体存到 dir/，供复核原始 HTML
  --delay <sec>      请求间隔，默认 5
  --timeout <sec>    单请求超时，默认 20
  --tls-echo         额外探测出口 TLS 指纹（发送到第三方回显服务）
  -h, --help         显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --selector)  SELECTOR="${2:-}"; shift 2 ;;
    --passes)    PASSES="${2:-1}"; shift 2 ;;
    --clients)   CLIENTS="${2:-}"; shift 2 ;;
    --save-html) SAVE_DIR="${2:-}"; shift 2 ;;
    --html)      HTML_FILE="${2:-}"; shift 2 ;;
    --delay)     DELAY="${2:-5}"; shift 2 ;;
    --timeout)   TIMEOUT="${2:-20}"; shift 2 ;;
    --tls-echo)  TLS_ECHO=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) URL="$1"; shift ;;
  esac
done

want() { [[ ",$CLIENTS," == *",$1,"* ]]; }

# 量内容区可见字符数（近似）。
# 输出：整数（命中）/ 0（容器在但无文字）/ missing（选择器没命中）/
#       ?unsupported（选择器过于复杂）/ ?（读文件失败）
content_len() {
  [[ -z "$SELECTOR" ]] && { printf '%s' "-"; return; }
  python3 - "$1" "$SELECTOR" <<'PY' 2>/dev/null || printf '%s' "?"
import re
import sys
from html.parser import HTMLParser

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr'}


class RegionLen(HTMLParser):
    def __init__(self, sel):
        super().__init__(convert_charrefs=True)
        self.sel = sel
        self.depth = 0
        self.active = False
        self.matched = False
        self.total = 0
        self.skip = 0

    def _match(self, tag, attrs):
        a = dict(attrs)
        if self.sel.startswith('#'):
            return a.get('id') == self.sel[1:]
        if self.sel.startswith('.'):
            return self.sel[1:] in (a.get('class') or '').split()
        return tag == self.sel

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
            return
        if not self.active:
            if self._match(tag, attrs):
                self.active = self.matched = True
                self.depth = 1
        elif tag not in VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
            return
        if self.active and self.depth > 0:
            self.depth -= 1
            if self.depth == 0:
                self.active = False

    def handle_data(self, data):
        if self.active and self.skip == 0:
            self.total += len(data.strip())


path, sel = sys.argv[1], sys.argv[2]
# 只支持简单选择器；复杂选择器宁可报不支持，也不要静默给 0
if not re.fullmatch(r'([A-Za-z][\w-]*)?(#[\w-]+)?(\.[\w-]+)*', sel):
    print('?unsupported')
    sys.exit(0)
if sel in ('', '#'):
    print('?unsupported')
    sys.exit(0)
try:
    html = open(path, 'rb').read().decode('utf-8', 'replace')
except Exception:
    print('?')
    sys.exit(0)
if not html:
    print('missing')
    sys.exit(0)
p = RegionLen(sel)
try:
    p.feed(html)
except Exception:
    pass
print(p.total if p.matched else 'missing')
PY
}

if [[ -n "$HTML_FILE" ]]; then
  [[ -z "$SELECTOR" ]] && { echo "--html 需要同时给 --selector" >&2; exit 1; }
  [[ -f "$HTML_FILE" ]] || { echo "找不到文件: $HTML_FILE" >&2; exit 1; }
  printf 'content\t%s\n' "$(content_len "$HTML_FILE")"
  exit 0
fi

if [[ -z "$URL" ]]; then usage; exit 1; fi
if ! command -v curl >/dev/null 2>&1; then echo "需要 curl" >&2; exit 1; fi

TMPDIR_M="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_M"' EXIT
BODY="$TMPDIR_M/body"
CURRENT_PASS=0

if [[ -n "$SELECTOR" ]]; then
  printf '%-26s\t%-8s\t%-5s\t%-10s\t%-12s\t%-7s\t%s\n' "client" "identity" "http" "bytes" "content" "time_s" "final_url"
else
  printf '%-26s\t%-8s\t%-5s\t%-10s\t%-7s\t%s\n' "client" "identity" "http" "bytes" "time_s" "final_url"
fi

reset_body() { : > "$BODY"; }

save_body() {
  [[ -z "$SAVE_DIR" || ! -s "$BODY" ]] && return
  mkdir -p "$SAVE_DIR"
  cp "$BODY" "$SAVE_DIR/p${CURRENT_PASS}-$1.html"
}

emit() {
  local client="$1" ident="$2" code="$3" size="$4" time="$5" final="$6" label="$7" clen
  save_body "$label"
  clen=$(content_len "$BODY")
  if [[ -n "$SELECTOR" ]]; then
    printf '%-26s\t%-8s\t%-5s\t%-10s\t%-12s\t%-7s\t%s\n' \
      "$client" "$ident" "$code" "$size" "$clen" "$time" "$final"
  else
    printf '%-26s\t%-8s\t%-5s\t%-10s\t%-7s\t%s\n' \
      "$client" "$ident" "$code" "$size" "$time" "$final"
  fi
}

sleep_between() { sleep "$DELAY"; }

# --- curl: 原生 TLS 栈 -------------------------------------------------------
probe_curl() {
  local identity="$1" ua="$2" out
  reset_body
  out=$(curl -sS -L --max-time "$TIMEOUT" -A "$ua" \
        -o "$BODY" \
        -w '%{http_code}\t%{size_download}\t%{time_total}\t%{url_effective}' \
        "$URL" 2>/dev/null) || out=$'000\t0\t0\tERR'
  IFS=$'\t' read -r code size time final <<<"$out"
  emit "curl(native)" "$identity" "$code" "$size" "$time" "$final" "curl-$identity"
}

# --- python requests: OpenSSL (urllib3) --------------------------------------
probe_py_requests() {
  local ua="$1" out
  reset_body
  out=$(python3 - "$URL" "$ua" "$TIMEOUT" "$BODY" <<'PY' 2>/dev/null
import sys, time
try:
    import requests
except Exception:
    sys.exit(3)
url, ua, timeout, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t0 = time.time()
try:
    r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout, allow_redirects=True)
    open(out, "wb").write(r.content)
    print(f"{r.status_code}\t{len(r.content)}\t{time.time() - t0:.3f}\t{r.url}")
except Exception as e:
    print(f"ERR\t0\t{time.time() - t0:.3f}\t{type(e).__name__}")
PY
) || out=$'SKIP\t0\t0\tnot-installed'
  [[ -z "$out" ]] && out=$'SKIP\t0\t0\tnot-installed'
  IFS=$'\t' read -r code size time final <<<"$out"
  emit "python-requests" "chrome" "$code" "$size" "$time" "$final" "py"
}

# --- node fetch (undici): OpenSSL -------------------------------------------
probe_node_fetch() {
  local ua="$1" out
  reset_body
  out=$(node -e '
const fs = require("fs");
const [url, ua, timeout, outPath] = [process.argv[1], process.argv[2], Number(process.argv[3]) * 1000, process.argv[4]];
const t0 = Date.now();
fetch(url, { headers: { "user-agent": ua }, redirect: "follow", signal: AbortSignal.timeout(timeout) })
  .then(async (r) => {
    const b = Buffer.from(await r.arrayBuffer());
    fs.writeFileSync(outPath, b);
    console.log([r.status, b.length, ((Date.now() - t0) / 1000).toFixed(3), r.url].join("\t"));
  })
  .catch((e) => console.log(["ERR", 0, 0, e.name].join("\t")));
' "$URL" "$ua" "$TIMEOUT" "$BODY" 2>/dev/null) || out=$'SKIP\t0\t0\tnode-missing'
  [[ -z "$out" ]] && out=$'SKIP\t0\t0\tnode-missing'
  IFS=$'\t' read -r code size time final <<<"$out"
  emit "node-fetch(undici)" "chrome" "$code" "$size" "$time" "$final" "node"
}

# --- curl_cffi: 浏览器级 TLS 指纹 -------------------------------------------
probe_curl_cffi() {
  local impersonate="$1" out
  reset_body
  out=$(python3 - "$URL" "$impersonate" "$TIMEOUT" "$BODY" <<'PY' 2>/dev/null
import sys, time
try:
    from curl_cffi import requests
except Exception:
    sys.exit(3)
url, imp, timeout, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t0 = time.time()
try:
    r = requests.get(url, impersonate=imp, timeout=timeout, allow_redirects=True)
    open(out, "wb").write(r.content)
    print(f"{r.status_code}\t{len(r.content)}\t{time.time() - t0:.3f}\t{r.url}")
except Exception as e:
    print(f"ERR\t0\t{time.time() - t0:.3f}\t{type(e).__name__}")
PY
) || out=$'SKIP\t0\t0\tnot-installed'
  [[ -z "$out" ]] && out=$'SKIP\t0\t0\tnot-installed'
  IFS=$'\t' read -r code size time final <<<"$out"
  emit "curl_cffi($impersonate)" "$impersonate" "$code" "$size" "$time" "$final" "cffi-$impersonate"
}

NPROBE=0
# 每次探测前统一节流（第一次除外），避免某类客户端缺失时白等
probe_wrap() {
  [[ "$NPROBE" -gt 0 ]] && sleep_between
  "$@"
  NPROBE=$((NPROBE + 1))
}

run_pass() {
  if want curl; then probe_wrap probe_curl "chrome" "$UA_CHROME"; fi
  if want curl; then probe_wrap probe_curl "safari" "$UA_SAFARI"; fi
  if want cffi; then probe_wrap probe_curl_cffi "chrome"; fi
  if want cffi; then probe_wrap probe_curl_cffi "safari"; fi
  if want node; then probe_wrap probe_node_fetch "$UA_CHROME"; fi
  if want py;   then probe_wrap probe_py_requests "$UA_CHROME"; fi
}

if ! want curl && ! want cffi && ! want node && ! want py; then
  echo "--clients 取值无效：可选 curl,cffi,node,py" >&2
  exit 1
fi

i=1
while [[ "$i" -le "$PASSES" ]]; do
  CURRENT_PASS=$i
  if [[ "$PASSES" -gt 1 ]]; then
    echo "# pass $i/$PASSES"
  fi
  run_pass
  i=$((i + 1))
  if [[ "$i" -le "$PASSES" ]]; then sleep_between; fi
done

if [[ -n "$SELECTOR" ]]; then
  cat <<'EOF'

content 列的取值：
  N             命中容器，可见字符数约 N
  0             容器存在但无文字（典型空壳）
  missing       选择器没命中（页面结构里没有这个容器）
  ?unsupported  选择器过于复杂，换简单选择器或改用真实浏览器取值
EOF
fi

cat <<'EOF'

读法：
  1) 同 UA、不同栈结果翻转      → 存在 L2 传输层闸门，按栈选客户端
  2) 同栈、不同 UA 结果翻转      → 存在 L3 身份规则
  3) 两类都不翻转但都失败        → 继续看 L4 运行时 / L1 链路 / 出口
  4) 2xx 但 content 为 0/极小     → 降级软拦截或抽取下游丢失，先按
     triage-and-gates.md「样本类型 1」判别序列排除 CSR / 抽取问题
  5) 4xx + 字节数极小            → 先看有无站点特征，无特征按链路失败复核
  6) 连打后整体变差             → 多半是频率降权，静置或换出口后重测
  7) 多 pass 出现通过↔拦截翻转   → 概率门，报区间与翻转率，别报单次点估计
EOF

if [[ "$TLS_ECHO" -eq 1 ]]; then
  echo
  echo "出口 TLS 指纹（第三方回显，按需使用）:"
  curl -sS --max-time 15 https://tls.peet.ws/api/all 2>/dev/null \
    | jq -r '{ip: .ip, ja3: .tls.ja3_hash, ja4: .tls.ja4} | to_entries[] | "  \(.key)=\(.value)"' 2>/dev/null \
    || echo "  (需要 jq，或回显服务不可达)"
fi
