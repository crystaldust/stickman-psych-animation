# 复盘：ECC 如何把一句话展开成完整开发流程

> 日期：2026-10-13
> 案例：开发 `bili-collect`（commit `de3e18f`、`2312e59`）
> 触发语句：「你可以调用 ECC 中的相关命令来新增这个功能」

---

## 1. 一句话总结

ECC 并不会「自动触发」什么。它提供三样东西：**可选的流程说明（skill）**、**预定义的审查角色（agent）**、**一直生效的规则和强制检查（rules / hooks）**。Claude 负责选对入口，然后照着流程说明执行。

---

## 2. 三层机制

### 第 1 层：skill 清单（入口由 Claude 选择）

每次会话开始时，所有已安装 skill 的名字和简介都会放进 Claude 的上下文里，ECC 插件贡献了其中几百个。收到「用 ECC 新增功能」后，Claude 对照了几个候选：

| skill | 用途 | 是否选用 |
|---|---|---|
| `ecc:orch-add-feature` | 从零新增功能：调研 → 规划 → TDD → 审查 → 提交，带两个关卡 | ✅ 最贴切 |
| `ecc:feature-dev` | 引导式功能开发 | — |
| `ecc:plan` | 只出方案 | — |

**这一步是 Claude 的判断。** 想要确定性地走某个流程，就直接输入斜杠命令，比如 `/ecc:orch-add-feature <需求>`。

### 第 2 层：skill 内容规定了流程

`orch-add-feature` 是一层很薄的包装，它要求加载共享引擎 `orch-pipeline`。后者的 SKILL.md 加载后成为执行说明，其中几条直接决定了这次的行为：

| 规定 | 本次的表现 |
|---|---|
| **规模分级**：涉及新外部依赖或公开接口的至少算 standard | 判定为 large（新依赖 yt-dlp / mlx-whisper，外部 API，空项目）→ 走全流程 |
| **关卡 1**：方案批准前不写实现代码 | 先做调研（测出 B 站风控 -352/412、本机有 ffmpeg、M4 芯片），给出方案后停下等批准 |
| **TDD**：每个任务先写失败的测试，再实现 | 先写测试并确认失败，再写实现让测试通过 |
| **各阶段用哪个 agent**：审查 = code-reviewer + 对应语言的审查 | code-reviewer + python-reviewer |
| **安全审查触发条件**：改动涉及用户输入 / 文件路径 / 外部 API / 凭证 | 读 cookies、拼文件路径、请求 B 站 → 加 security-reviewer |
| **关卡 2**：提交前需用户确认 | 汇总改动和拟提交的 commit 信息后停下 |

文件位置：`~/.claude/plugins/cache/ecc/ecc/2.2.1/skills/orch-pipeline/SKILL.md`

### 第 3 层：全局规则和 hooks（一直生效）

**规则**（`~/.claude/rules/common/*.md`，每次会话都会加载）：

- `agents.md`：互相独立的操作**必须并行执行** → 三个审查放在同一条消息里发出
- `testing.md`：先写测试，覆盖率 80% 以上
- `code-review.md`：写完代码必须审查，CRITICAL 和 HIGH 问题修完才能提交
- `coding-style.md`：优先不可变数据，函数不超过 50 行，文件不超过 800 行

**Hooks**（Claude Code 在工具调用前后强制执行，不是提示词）：

- **GateGuard（Fact-Forcing Gate）**：首次创建或修改文件、首次执行 Bash 命令前，要求先陈述「调用方 / 数据结构 / 用户原话」。这次一共拦截了约 20 次。
- **StrategicCompact**：工具调用次数和上下文变大时提醒压缩上下文。
- **成本提醒**：会话花费超过 $10 时提示。

---

## 3. 完整链路

```
用户：「调用 ECC 中的相关命令来新增这个功能」
  │
  ├─ Claude 从 skill 清单选出 orch-add-feature        ← Claude 的判断
  │     └─ 加载 orch-pipeline                         ← skill 文本
  │           ├─ 规模判定为 large → 全流程
  │           ├─ 调研 → 方案 → 【关卡 1】用户批准
  │           ├─ TDD 实现（先失败，后通过）
  │           ├─ 审查 = code-reviewer + python-reviewer
  │           │     + security-reviewer（触发安全条件）
  │           └─ 【关卡 2】用户确认后提交
  │
  ├─ rules/agents.md：独立任务必须并行                 ← 全局规则
  │     └─ 三个 Agent 调用放在同一条消息里 → 并行执行
  │
  └─ hooks（GateGuard 等）在每次写文件、执行命令时强制检查
```

---

## 4. 多 agent 并行是怎么实现的

### 角色定义 = 一个 Markdown 文件

`~/.claude/plugins/cache/ecc/ecc/2.2.1/agents/security-reviewer.md`：

```yaml
---
name: security-reviewer
description: Security vulnerability detection ... Use PROACTIVELY after writing code that handles user input...
tools: Read, Grep, Glob, Bash      # 没有 Edit/Write，只能读不能改
model: sonnet
---
（下面是系统提示词：检查清单、输出格式……）
```

### 执行机制

1. **并行**：在同一条回复里调用三次 Agent 工具（`subagent_type` 分别为 `ecc:code-reviewer`、`ecc:security-reviewer`、`ecc:python-reviewer`）。同一条消息里互相独立的工具调用会同时执行。
2. **后台运行**：主会话继续做别的事（写了 README）。
3. **独立上下文**：每个子代理都从干净的上下文开始，看不到之前的对话，所以任务说明必须写清楚路径、范围和重点。
4. **只交回结论**：子代理自己读代码、跑测试，完成后只把最终报告交回主会话，并发出完成通知。
5. **由主会话汇总和修改**：去重、合并成修复清单、由主会话按 TDD 修复。只有主会话改代码，避免多个 agent 同时改同一个文件。

### 本次的实际收益

| 维度 | 数据 |
|---|---|
| 耗时 | 三个审查各 40–80 秒，并行总耗时约等于最慢的那个 |
| 上下文 | 三个子代理一共读了约 12 万 token，主会话只收到几千字的报告 |
| 发现的问题 | 0 CRITICAL / 5 HIGH / 9 MEDIUM（去重后），三份报告重叠很少 |

各角色的代表性发现：

- **security-reviewer**：同时传 `--cookies-from-browser` 和 `--cookies` 时，yt-dlp 退出时会把浏览器里**所有网站**的 cookies 明文写进文件。
- **code-reviewer**：yt-dlp 的中间文件（`video.f123.mp4`、`.temp.mp4`、`.part-Frag`）和被中断的半截 `audio.wav` 会被当成已完成，导致断点续跑永远不会修复它们。
- **python-reviewer**：`Context.client: Any` 没有类型约束；`CollectReport` 只是浅层不可变；批量采集遇到风控不会中止。（「风控不中止」这一条 code-reviewer 也报了。）

---

## 5. 经验与改进点

**有效的做法**

- **两个关卡**让人只在关键决策点介入：批准方案、确认提交。中间的流程不需要人参与。
- **真实冒烟测试不可省**：单元测试全部通过之后，真实运行又暴露了三个问题：B 站偶发「No video formats found」、Whisper 一段长达 28 秒、扁平列表没有标题。
- **审查 agent 的任务说明写清楚重点**（断点续跑逻辑、cookies、路径），报告质量明显更高。

**摩擦点**

- GateGuard 对每个新文件都要求陈述，批量创建 8 个模块时会被连续拦截。可以考虑在 `GATEGUARD_EXEMPT_GLOBS` 里豁免 `tests/**`，或者在新建模块前一次性陈述完。
- 带 cookies 的真实列表请求需要用户在本机授权钥匙串访问，agent 无法自己验证。

**下次怎么用**

| 想要的效果 | 怎么做 |
|---|---|
| 确定性地走某个流程 | 直接输入 `/ecc:orch-add-feature <需求>`（或 `orch-fix-defect` / `orch-change-feature` / `orch-refine-code`） |
| 指定审查角色 | 「用 security-reviewer 和 code-reviewer 并行审这次改动」 |
| 自定义角色 | 在 `.claude/agents/<名字>.md` 里写 frontmatter 和提示词，比如一个「文案风格审查」agent |
| 大规模编排 | 明确要求「用 workflow」，用脚本编排多组 agent。token 消耗会多很多 |
