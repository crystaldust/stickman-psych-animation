# 火柴人动画 YouTube 频道：工作流与成本调研

> 调研日期：2026-10-13
> 资料来源：3 个 YouTube 教程的完整字幕，加上工具官网和第三方定价页面
> 说明：价格以调研当天能查到的为准，第三方来源之间常有出入，购买前请以官网为准

---

## 1. 调研对象

| # | 视频 | 频道 | 时长 | 重点 |
|---|---|---|---|---|
| A | [Claude Code Just Changed YouTube Forever!](https://www.youtube.com/watch?v=WODnqHPLR38) | Danny Why | 13 分钟 | 用 Claude Code + Higgsfield 自动批量出图 |
| B | [How I Make $7,528/Month Posting Stickman YouTube Videos](https://www.youtube.com/watch?v=2_lvAS0_Is0) | Kevis Unfiltered | 18 分钟 | 选题方法、ChannelRecipe 一键生成 |
| C | [How to Make Stickman Animation with AI 100% FREE](https://www.youtube.com/watch?v=jglonMYLI6g) | Money Degree | 24 分钟 | 免费工具的完整流程（方法一）、Vsub 一键生成（方法二） |

三位作者都有商业利益：A 的描述里是 Higgsfield 推广链接；B 推的 ChannelRecipe 和 headstartchannels（代售预变现频道）是他在卖的产品或服务；C 的工具链接大多是推广链接。

---

## 2. 赛道特征

- **爆款题材**：「古人 / 史前人类怎么做某件事」，比如古人晚上做什么、无聊时做什么、没有牙刷怎么护牙。
- **画面风格**：MS Paint 风格的粗糙火柴人，白色背景，**每 2–3 秒换一张图**。真正关键的是图多、换得快，而不是画得好。
- **视频结构**（B 的拆解）：钩子问题 → 「一天的生活」视角讲过程 → 「哪里出了问题 / 后来怎样」 → 给现代人的启发。
- **时长**：8–15 分钟，8 分钟以上才能插中插广告。
- **竞争情况**：大量新频道在发 10 来条视频后就起量，说明需求大于供给。但教程传播得很广，**同题撞车正在加剧**。

---

## 3. 各视频要点

### A. Danny Why：Claude Code + Higgsfield

1. 安装 Claude Code 桌面版，切换到 **Code** 模式（不要用 Chat 或 Cowork）。
2. 注册 Higgsfield，进入 MCP & CLI → CLI，在终端依次执行三条命令：
   - 安装 CLI
   - `higgsfield auth login` 授权登录
   - `npx skills add higgsfield-ai/skills` 安装 skill
3. 准备**带时间戳的脚本**和配音。视频里没说时间戳怎么来的，第三方总结写的是 TurboScribe 转写。
4. 把「主提示词」和带时间戳的脚本一起贴进 Claude Code。主提示词的要点：
   - 脚本里每个时间戳生成一张图
   - 画风像不会画画的人用 MS Paint 随手画的
   - 白色背景，不要 3D，不要电影感
5. Claude Code 调用 Higgsfield 批量生成图片。一份完整脚本会生成 100 张以上，作者说这一步大约 10 分钟。
6. 告诉 Claude Code：「把图片下载到本地，文件名用对应的时间戳」。
7. 在剪辑软件里放好配音，按文件名的时间戳把图片排上时间线。作者说大约 10 分钟。

### B. Kevis Unfiltered：选题与 ChannelRecipe

1. **选题**：在 Claude 里连上 **vidIQ** connector，对竞品频道做 niche evaluation 或竞品拆解，再让 Claude 结合爆款规律生成本周的新选题。
2. **差异化**：照搬爆款的**格式**，换掉**题目**。比如「古人 + 无聊 / 孤独 / 被拒绝」，从自己真正感兴趣的角度来组合。
3. **缩略图**：截一张爆款缩略图作为参考图，在 Google Labs（Flow）里选 16:9，输入标题生成同风格的缩略图。
4. **ChannelRecipe**：
   - 在 Script Studio 里输入竞品频道 URL，让它学习该频道的 20 条视频，然后生成标题和脚本。
   - 新建视频时选 Animated Explainer 模板，选配音，由工具自动渲染成片。
   - 最后导出 upload kit（标题、描述、标签）。
5. **运营**：日更，接受前期播放量平淡，出现一个爆款后加倍做同类选题。作者推荐买「预变现频道」，**本文不建议这样做**（见第 7 节）。

### C. Money Degree：免费流程与 Vsub

**方法一：免费工具链**

1. 在 Claude 里贴入作者的「master prompt」，再上传「virality / consistency / production bible」资源文件（两者都要进他的 Telegram 群领取）。
2. Claude 依次引导：列出适合火柴人风格的 niche → 给出视频选题 → 确定时长（建议 8–15 分钟）→ 写出完整脚本。
3. **配音**：DupDub 的 AI Voiceover，粘贴脚本前先删掉多余空行，选 Ultra 筛选下的 Liam 声音，导出 MP3。
4. **生图提示词**：让 Claude 按脚本输出一组提示词，并算好需要多少张图（2 分钟视频约 35 张，约每 3.4 秒一张）。
5. **出图**：在 Google Flow 里选 16:9 批量生成，下载原尺寸。
6. **动画**：让 Claude 写图生视频的动画提示词，在 Flow 里用 **Veo Lite** 模型（免费账号可用）。**只做前 5–10 个镜头**，用来抓住开头的注意力。
7. **剪辑**：在 CapCut 里先放配音（保留自然停顿），再按脚本顺序放视频片段和图片，最后一张图加淡出后导出。
8. **包装**：让 Claude 生成标题、描述、标签和缩略图提示词。

**方法二：Vsub 一键生成**

1. 用 Claude 写好脚本（流程同方法一）。
2. 在 Vsub 里选 AI Video v3，比例 16:9，模板选 Stickman Ancient；也可以上传参考视频做自定义模板。
3. 粘贴脚本，选配音（视频里用的是 Todd 声音），工具自动生成全部镜头并剪好。
4. 用 pro 模式给开头 5–10 张图做动画，打开 fixed camera，大约覆盖前 18 秒。
5. 配音音量设到 55% 并打开 sync volume；不加转场，不加背景音乐，字幕可选。
6. 导出 MP4，60fps（选 4K 会多扣 credits）。

---

## 4. 综合工作流（推荐）

以一条 10 分钟视频为例：

| 步骤 | 做什么 | 推荐工具 | 免费替代 |
|---|---|---|---|
| 1. 选题 | 拆解竞品，按「古人 + 现代痛点」公式出选题 | Claude + vidIQ connector | 手动浏览竞品频道 |
| 2. 脚本 | 8–15 分钟，套用爆款结构；把风格写成一份自己的 production bible，每期复用 | Claude Pro | Claude 免费版 |
| 3. 配音 | 自己录（变现风险最低），或用 AI 配音 | 麦克风 / DupDub / ElevenLabs | 自己录 |
| 4. 时间戳 | 把配音转写成带时间戳的句子 | TurboScribe | **本地 Whisper**（完全免费） |
| 5. 出图 | 每个时间戳一张图，文件名就是时间戳，共约 200–300 张 | Claude Code + Higgsfield | Google Flow 手动批量生成 |
| 6. 开头动画 | 前 5–10 张图做图生视频 | Flow Veo Lite / Higgsfield | Flow 每天的免费 credits |
| 7. 剪辑 | 按时间戳把图片排上时间线，不加转场，结尾淡出 | CapCut | CapCut 免费版 / DaVinci Resolve |
| 8. 包装 | 标题、描述、标签；参考爆款截图生成缩略图 | Claude + Flow | 同左 |
| 9. 发布 | 日更或高频更新，出现爆款后加倍做同类选题 | YouTube Studio | — |

### 可以用代码自动化的部分（本项目的潜在方向）

第 4 到第 7 步都可以写成脚本流水线：

```
配音.mp3
  → Whisper 生成带时间戳的文本
  → Claude 按句子生成提示词
  → Higgsfield CLI 批量出图（文件名 = 时间戳）
  → ffmpeg 或 Remotion 按时间戳自动拼接，开头几张做 Ken Burns 推拉
  → 成片.mp4
```

这样能把手工剪辑的 10 分钟也省掉，人只需要负责选题、脚本和录音这几个有差异化的环节。

---

## 5. 工具价格（2026-10 查询）

| 工具 | 用途 | 价格 | 备注 |
|---|---|---|---|
| Claude Pro | 脚本、Claude Code | $20/月 | Claude Code 需要 Pro 及以上 |
| Higgsfield | 批量出图 / 出视频 | Free $0（**不能商用**）/ Starter 约 $19（年付，270 credits）/ Plus $59 月付或 $47 年付（1,200 credits）/ Ultra $129 月付或 $99 年付（3,000 credits） | 订阅 credits 当月不用完作废；每张图扣多少 credits 取决于模型，**需要实测** |
| Google Flow | 出图、图生视频 | 免费每天 50 credits；Google AI Pro $19.99/月（1,000 credits） | Veo 3.1 Lite 每次 10 credits / Fast 20 / Quality 100；Google 经常调整额度 |
| TurboScribe | 转写时间戳 | 免费每天 3 个文件；付费约 $10–20/月 | 可以用本地 Whisper 代替 |
| DupDub | AI 配音 | 3 天试用；Personal $15/月（150 credits）；Professional $40/月 | C 视频说的「100% 免费」实际只是试用 |
| ChannelRecipe | 一键生成整条视频 | Starter $27/月（15 条）；Daily $49/月（35 条） | 7 天免费试用；每条视频 1 credit，约 $1.4–1.8/条 |
| Vsub | 一键生成整条视频 | $29 / $49 / $99 每月（年付 $20 / $34 / $69） | Starter 最多导出 2 分钟，Pro 10 分钟，Premium 20 分钟；Trustpilot 上有 credits 不够用的投诉 |
| vidIQ | 选题数据 | 有免费版 | 通过 Claude connector 使用 |
| CapCut | 剪辑 | 免费版够用；Pro 约 $10–20/月 | — |

### 三条路线的月成本

| 路线 | 组合 | 月成本 | 适合 |
|---|---|---|---|
| 全免费 | Claude 免费版 + Flow 免费额度 + 自己录音 + Whisper + CapCut | $0 | 先做 1–3 条试水，手工多，一条要几个小时 |
| **半自动（推荐）** | Claude Pro + Higgsfield Starter 或 Plus + 自己录音 + CapCut 免费版 | **$40–80** | 稳定周更甚至日更 |
| 一键生成 | Claude Pro + ChannelRecipe 或 Vsub | $50–120 | 追求量，能接受同质化 |

**待验证**：一条 10 分钟视频需要 200–300 张图，Higgsfield Starter 的 270 credits 可能只够 1–2 条。建议先订一个月 Starter，完整做一条，看实际扣了多少 credits，再决定买哪一档。

---

## 6. 关键经验汇总

1. **时间戳驱动是整个流程的核心**：图片文件名等于时间戳，剪辑就变成了机械对位，这也是能自动化的前提。
2. **图多比图好重要**：每 2–3 秒换一张图，保持画面节奏。
3. **只给开头做动画**：前 15–20 秒决定观众会不会留下来，后面用静态图就够了。
4. **差异化在选题和脚本**：工具人人都能用，竞争力来自题目角度和脚本质量。
5. **把风格固化成文档**：写一份自己的 production bible（风格、结构、禁忌、提示词模板），每期复用，保证产出稳定。
6. **8 分钟以上**：为了中插广告，同时保持高频更新。

---

## 7. 风险

| 风险 | 说明 | 应对 |
|---|---|---|
| 收入数据不可信 | $7.5k/28 天、$6 万/月这些数字都来自截图或 vidIQ 估算，作者们都靠卖工具或服务赚钱，存在幸存者偏差 | 按「可能不赚钱」做预算，先用低成本验证 |
| YouTube 不真实内容政策 | YouTube 会对「不真实 / 重复内容」（inauthentic / repetitious content）取消变现，批量 AI 模板内容是重点审查对象 | 原创选题和脚本，尽量用自己的声音，避免一键模板量产 |
| AI 配音 | A 视频作者认为用 ElevenLabs 这类 AI 配音有被取消变现的风险 | 优先自己录音 |
| 购买预变现频道 | 来路不明，频道可能被收回，也可能违反平台规则 | **不建议** |
| 工具的商用授权 | Higgsfield 免费版不允许商用 | 变现前必须切到付费版 |
| 赛道饱和 | 教程传播广，同题竞争会加剧 | 尽早入场，并扩展到「古人」以外的同类格式 |

---

## 参考来源

- 视频 A：https://www.youtube.com/watch?v=WODnqHPLR38
- 视频 B：https://www.youtube.com/watch?v=2_lvAS0_Is0
- 视频 C：https://www.youtube.com/watch?v=jglonMYLI6g
- 视频 A 的第三方步骤总结：https://jacche.com/claude-code-youtube-62k-month-ai-guide/
- Higgsfield CLI 帮助文档：https://higgsfield.ai/creator-hub/help-center/mcp-cli/how-do-i-access-higgsfield-via-cli
- Higgsfield Stickman skill：https://higgsfield.ai/skills/stickman-cartoon
- Higgsfield 定价：https://creatify.ai/blog/higgsfield-pricing-(2026)-plans-and-what-you-ll-actually-pay
- Google Flow credits：https://www.mindstudio.ai/blog/google-flow-pricing-credits-tiers-explained
- ChannelRecipe：https://channelrecipe.com/
- Vsub 定价：https://creatoreconomytools.com/tool/v-sub
- DupDub 定价：https://www.softwaresuggest.com/dupdub/pricing
