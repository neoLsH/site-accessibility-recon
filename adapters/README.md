# 适配层：让同一个 skill 在多个 Agent 里跑

核心只有一份：仓库根目录的 `SKILL.md` + `references/` + `scripts/`。

适配层是**薄壳**，只做“指针 + 路径”，不复制内容。否则多处副本必然漂移。

## 支持矩阵

| Agent | 加载方式 | 要做什么 |
|---|---|---|
| Claude Code | Agent Skills（`SKILL.md`） | 把本目录放进 skills 目录 |
| Codex CLI | Agent Skills（实验性支持）+ `AGENTS.md` | 优先放 skills 目录；不支持时用 `AGENTS.md` 兜底 |
| Gemini CLI | Skills（`activate_skill`） | 放 skills 目录 |
| Copilot CLI | Skills（`skill` 工具） | 放 skills 目录 |
| Cursor / Windsurf / 其他 | `AGENTS.md` / 规则文件 | 指向本仓库 `AGENTS.md` |
| 任意 Agent | 手动 | 让它读 `SKILL.md` |

> Agent 的 skill 目录位置随版本变化，本仓库不做硬编码。用 `install.sh <目标目录>` 显式指定；不确定时查你这个 Agent 的文档。

## 安装

```bash
# 项目级（推荐，团队共享）
./adapters/install.sh .claude/skills

# 用户级（所有项目可用）
./adapters/install.sh ~/.claude/skills

# 其他 Agent 同理，把目标目录换成它读取 skills 的目录
./adapters/install.sh ~/.codex/skills
```

脚本做的事：在目标目录下创建一个指向本仓库的符号链接（不支持符号链接的平台改为复制，并打印提醒）。

## 只用 AGENTS.md 的 Agent

在项目根目录的 `AGENTS.md` 里加一行指针即可：

```markdown
站点可访问性排查：先读 <本仓库路径>/AGENTS.md，按其中的硬规则执行。
```

## 为什么不做成每 Agent 一份提示词

- 内容会漂移：修了 `SKILL.md`，其他副本不会跟着改。
- 触发描述要一致：`description` 是技能被选中的唯一依据，多份描述会导致行为不一致。
- `references/` 的按需加载是核心设计，复制品通常会被压成一个大 prompt，反而丢掉分层。

## 版本

`SKILL.md` frontmatter 的 `metadata.version` 是唯一版本号。适配层不单独版本化。
