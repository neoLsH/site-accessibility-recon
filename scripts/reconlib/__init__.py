"""reconlib — 可访问性探索的原子能力。

每个原子只做一件事，输入输出都是普通字典，便于 fetch / crawl / bandit 复用与单测：
    measure   内容区字符数（不量整页，否则导航与页脚会把空壳撑成有内容）
    classify  一次响应 → 证据行 outcome + WAF 机制类别
    robots    robots.txt 合规判定
    fetch     单次网络取样（原生 curl / curl_cffi 协议栈）

红线在原子层就要守住：不做验证码求解、不轮换出口、不伪造挑战 cookie、不绕鉴权。
"""

UA_DEFAULT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
