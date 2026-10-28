# recon/ — 一次排查的工作区

排查的全部记录都放这里。**内容默认不进版本库**，只有这份说明和目录骨架值得提交。

```
recon/
├── .gitignore        # 除骨架外全部忽略
├── README.md         # 本文件
├── raw/              # 原始响应体，文件名带页面前缀（p1-curl.html、p1-node.html）
└── <代号>/           # 一个目标一个目录
    ├── profile.yaml  # P0 站点档案，机器可读，交给执行侧消费
    ├── evidence.csv  # 证据矩阵原始行
    ├── report.md     # 人读报告，由 report.py 生成
    └── notes.md      # 推理链、误判、未解问题
```

## 三条规矩

1. 目录名用**代号**（`forum`、`ticket`），不用域名：报告要外发或晋升成案例时不用改名，也不会顺手漏出站点标识。
2. 原始响应体只进 `raw/`，不要内联进档案或报告；档案里不写站点内部常量（site id / hash / 签名密钥）与 Cookie 名值。
3. 一个代号对应一个口径。登录态 / 匿名、不同地域、不同身份要分开，否则双指标会被混算。

## 怎么用

```bash
# 首次：只落地骨架（-R 带点，确保 .gitignore 一起复制过去）
cp -R <skill 目录>/assets/workspace/. ./recon/

scripts/client-matrix.sh <url> --selector '#content' --save-html recon/raw
scripts/report.py --profile recon/<代号>/profile.yaml --out recon/<代号>/report.md
```

重来一次：删掉 `<代号>/` 和对应的 `raw/` 文件即可，骨架不动。

## 为什么不在 skill 目录里

skill 目录会被 `npx skills add` 拷进各 Agent 的 skills 目录，`skills update` 更新时整份重写。记录放进去，一次更新就没了。
