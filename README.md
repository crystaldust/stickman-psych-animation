# stickman-video

用 AI 流水线做一个讲心理学和亲密关系的火柴人动画频道（faceless，不露脸）：涂鸦火柴人画面，配 CRT / 胶片质感、沉稳的深夜旁白和 Midwest Emo 风格的 BGM。目前是试点阶段，每条 1–3 分钟，中文为主版本，另出一条英文版给 YouTube 的全球观众。

分工：**人**负责选题、定稿、试听多音字；**Claude Code** 负责拆解参考文案、核实出处、写稿、分镜和翻译；**Python 命令**（`stickman`）只做确定性的事，也就是采集、校验、查重、调接口和用 ffmpeg 剪辑。

## 整体流程

```
① 采集参考文案   stickman bili collect                → data/<mid>/<bvid>/transcript.txt
② 拆解与核实     /analyze-script（派 source-hunter）  → analysis/<bvid>.json
                 stickman library build              → library/sources.jsonl（出处库）
③ 写原创文案     /write-script <选题>                 → drafts/<日期>-<短标题>.md
                 stickman draft check                → 篇幅统计 + 查重
   ── 人审稿、定稿 ──
④ 拆出旁白与分镜 stickman video init <草稿>          → narration.txt + shots.json 骨架
⑤ 填写分镜       Claude 按「> 画面」填 bg / scene / motion → stickman video check
⑥ 出图 ∥ 配音    stickman video render               → img/ layers/ voice/
⑦ 时间线与字幕   （render 内）裁停顿、算镜头切点      → voice.wav、subs.ass、subs.srt
⑧ 渲染成片       （render 内）逐镜头片段 → 拼接 → 混 BGM → data/videos/<slug>/<slug>.mp4
⑨ 检查与返工     看联系表 → stickman video reshoot <dir> <镜头>
⑩ 英文版         /translate-script → 人审译文 → render --lang en → <slug>.en.mp4
⑪ 发布信息       /package-video → publish.json → stickman video package → 标题、简介、封面图
```

外部服务：火山方舟 **Seedream**（出图）、豆包 **seed-audio**（配音，复刻音色）、本机 **mlx-whisper**（转写参考文案）、本机 **ffmpeg**（剪辑）。

## 环境

- macOS + Apple Silicon（转写用 mlx-whisper，编码用 `h264_videotoolbox`）
- Python 3.12、[uv](https://docs.astral.sh/uv/)、ffmpeg（`brew install ffmpeg`）
- 第一次转写会下载 `mlx-community/whisper-large-v3-turbo` 模型（约 1.6GB）
- API key 只从 macOS 钥匙串读，不写进任何文件：`ark-api-key`（出图）、`volc-tts-key`（配音）
- 封面字体：[得意黑 Smiley Sans](https://github.com/atelier-anchor/smiley-sans)（OFL，可商用），不入 git，要自己下载放到 `data/fonts/`：

```bash
uv sync
uv run stickman --help
mkdir -p data/fonts && curl -sSL -o /tmp/smiley.zip \
  https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip \
  && unzip -o -j /tmp/smiley.zip SmileySans-Oblique.ttf -d data/fonts/
```

统一入口是 `stickman <子命令>`：`bili`（采集）、`corpus` / `analysis` / `library` / `draft`（文案）、`video`（做视频）。

---

## ① 采集参考文案（stickman bili）

从参考频道（B 站 up 主）采集视频元数据和音频，用 Whisper 转写成带时间戳的文案，作为研究选题和文案结构的语料。**只用于个人调研，不搬运、不重新上传他人作品**；写稿时也不读这些文案找灵感，查重会挑出照搬的地方。

`bili-collect` 是 `stickman bili` 的别名，下面的示例用 `bili-collect`。

```bash
uv run bili-collect list https://space.bilibili.com/3546974439607171 --limit 20   # 列出视频并写入索引
uv run bili-collect fetch BV1CSWXzAEys --transcribe                               # 单个视频：元数据 + 转写
uv run bili-collect collect 3546974439607171 --limit 30 --transcribe              # 批量，可断点续跑
```

- `fetch` 和 `collect` 都支持 `--video` / `--audio` / `--transcribe`，可以任意组合。
- 全局选项写在子命令**之前**：`bili-collect --sleep 2 collect ...`；用 `stickman` 入口时写在 `bili` 之后：`stickman bili --sleep 2 collect ...`。全局选项有 `--data-dir`（默认 `data`）、`--sleep`（请求间隔，默认 1.0 秒）、`--model`（Whisper 模型）、`--no-subtitles`、`--cookies-from-browser`、`--cookies`、`-v`。
- 支持的输入：BV 号、视频链接（可带 `?spm_id_from=...`）、b23.tv 短链、mid、空间链接。

**文案从哪来**

- 主文案由 Whisper 生成：有标点、一句一行，适合阅读和分析。结果会自动去掉片尾音乐段的幻觉句（如「请不吝点赞 订阅 转发…」），并纠正常见人名错写（见 `transcribe.CORRECTIONS`）。
- 登录时（`--cookies-from-browser chrome`）还会另存 B 站官方字幕 `subtitles.<语言>.srt` 作为对照。官方字幕没有标点、常在句中断开，只用来核对人名。不需要时加 `--no-subtitles`。
- 想用新规则重新转写某篇：删掉它目录下的 `transcript.json` 再跑一次。

**批量与风控**

- `collect` 是流水线：下载在后台线程，Whisper 在主线程逐个转写（实测快约 20%）。`--jobs N`（1–4）控制同时访问 B 站的任务数，瓶颈在 Whisper，一般不用调。
- 断点续跑看最终产物文件名，`.part`、`.temp`、`.fNNN` 等中间文件不算完成。
- B 站对未登录请求会间歇性返回 -352 / 412。遇到时批量任务立即中止，改用已登录浏览器的 cookies 重试：`bili-collect --cookies-from-browser chrome list ...`。cookies 只在本地使用；yt-dlp 只拿到一份临时副本，不会改写原文件。

```
data/<mid>/videos.jsonl                 # 视频索引
data/<mid>/<bvid>/meta.json             # 简介、标签、播放、点赞等
data/<mid>/<bvid>/audio.wav             # 16kHz 单声道（source.m4a 是下载的音频流）
data/<mid>/<bvid>/transcript.txt|srt|json   # 主文案，[mm:ss] 一句一行
data/<mid>/<bvid>/transcript.info.json  # 主文案用的 Whisper 模型
data/<mid>/<bvid>/subtitles.<语言>.srt  # B 站官方字幕对照（可能不存在）
```

## ② 拆解参考文案、建出处库

在 Claude Code 里运行：

```bash
/analyze-script BV1CSWXzAEys     # 分析一篇
/analyze-script pending          # 分析所有还没分析的
```

skill 会把文案拆成「钩子 / 概念 / 例子 / 换个角度 / 收尾」几段，列出每一处引用（「某人说过」「某书里写道」）和点名的概念，然后**并行派 `source-hunter` agent 联网核实每条引用**，判定为：

| 判定 | 含义 | 写稿能不能用 |
|---|---|---|
| `verbatim` 原话 | 找到了对应原文 | 能 |
| `paraphrase` 意译 | 原文有这个意思，措辞不同 | 能 |
| `misattributed` 误引 | 出处不对，或被当原话说但找不到原句 | 只能作为「流传很广但其实……」来讲 |
| `unverified` 无法核实 | 找不到可靠出处 | 不能 |
| `no_attribution` 未注明出处 | 作者自己的观点 | 不需要核实 |

结果先写到 `analysis/.drafts/<bvid>.json`，再用 `stickman analysis save` 校验格式后保存到 `analysis/`。分析完一批后运行 `stickman library build`，按「作者 + 作品」把所有核实过的引用汇总成出处库 `library/sources.jsonl`，写稿时从这里挑观点。

```bash
uv run stickman corpus list|show <BV>|export --output out/corpus.md
uv run stickman analysis list|show <BV>|save <file>
uv run stickman library build|show
```

## ③ 写原创文案

```bash
/write-script 分手后总想弄明白为什么
```

`write-script` skill 的步骤：

1. 读 `docs/style-guide.md`（硬性约束）和出处库，挑 1–2 个和选题相关、判定为原话或意译的观点。没给选题时，先根据出处库给 3 个选题建议。
2. 出处库里没有合适的，可以联网找新出处，但必须先派 `source-hunter` 核实；草稿 front matter 里注明「新增出处，未进出处库」。
3. 按风格指南的结构写：钩子 → 承认 → 一个观点 → 例子 → 换个角度 → 收尾。650–1300 字，**一句一行**，每段后面一行 `> 画面：…` 写这段配什么画面。
4. 运行查重，按结果改写，直到通过。
5. 把全文、出处和判定、查重结果交给人审。人定稿后把 front matter 的 `status` 改成 `final`。

```bash
uv run stickman draft check drafts/20261019-tui-kai.md
```

- 第一行报告正文字数和预计时长（不算 front matter 和画面行），`duration_estimate` 以它为准。
- 查重：去掉标点后，连续 15 字以上和任一篇已采集文案相同就报警（退出码 1）。60 字以内、成对中文引号里的直接引用不计。没有可比较的文案时退出码 2，不能当作通过。

风格指南里几条和配音直接相关的写法（第 5.1 节）：

- 书名号后面紧跟的字不能和书名最后一个字连成词（「《三字经》过了一遍」会被读成「三字 经过」）。
- 引号里是一整句话、后面直接接旁白时，在 」 后面加逗号，否则配音不停顿，会连读下去。

草稿示例：

```markdown
---
title: 越怕失去一个人，越容易把ta推开
topic: 借默顿「自我实现的预言」来讲恋爱里的试探
duration_estimate: 约 1 分 34 秒（660 字，stickman draft check 实测）
sources:
  - author: 罗伯特·默顿
    title: 自我实现的预言
    locator: The Self-Fulfilling Prophecy, The Antioch Review 8(2), 1948, p. 195
    verdict: verbatim
status: draft
---

ta两个小时没回消息，
你已经在心里，把分手的话都替ta想好了。
> 画面：深夜，火柴人坐在床边盯着手机，头顶冒出一串越来越大的问号
```

---

## ④ 从草稿生成旁白（narration）和分镜骨架

```bash
uv run stickman video init drafts/20261019-tui-kai.md
```

生成工作目录 `data/videos/<草稿文件名>/`：

- **`narration.txt`**：去掉 front matter 和画面行，正文**一行一句**原样写入。这就是配音和字幕的唯一来源，之后改旁白直接改这个文件。
- **`shots.json`**：每条 `> 画面` 生成一个镜头。画面提示写在它所配的旁白之后，所以一个镜头从上一条提示后的第一行旁白开始，到这条提示为止；`line` 记的是镜头第一行旁白的行号。骨架里 `bg`、`scene` 是空的，`cue` 是草稿里的画面提示。

```json
{"style": "参考图中火柴人的角色造型和画风：……背景是纯色平涂的{bg}……画面内容：",
 "shots": [{"id": "s01", "line": 1, "cue": "深夜，火柴人坐在床边盯着手机……", "bg": "", "scene": ""}]}
```

## ⑤ 填写分镜

在 Claude Code 里按每个镜头的 `cue` 和对应旁白，填好：

- `bg`：背景色，比如「深蓝色」「暖橙色」。
- `scene`：画面内容。人物的身体结构要写明（几个火柴人、各自在哪、在做什么），情绪靠头部角度和表情表达，不写「缩着肩膀」这类会让火柴人身体变形的词。
- `motion`（可选）：少数镜头改为分层动画，见下面「分层镜头」。

```bash
uv run stickman video check data/videos/20261019-tui-kai
```

`check` 校验行号、必填字段、`motion` 参数，以及分层镜头不超过一半。**没填完的骨架永远不会被拿去调付费接口。**

## ⑥ 图片怎么生成

`render` 为每个缺图的镜头调一次 Seedream（默认 8 路并发）：

- **提示词** = `shots.json` 的 `style`（把 `{bg}` 换成这个镜头的背景色）+ `scene`。`style` 来自 `config.py` 的 `STYLE_PROMPT`，写死了画风和构图：超大白色圆头、圆圈小黑点眼睛、粗细不均的马克笔描边、单线条四肢；扁平、无阴影、像 MS Paint 随手画的；16:9 中景，火柴人约占画面高度三分之一，画面下方五分之一留空给字幕；纯色平涂背景配极少的简笔道具；**不要文字**（字幕会和乱码字母打架）、不要写实、3D、水彩和光影。
- **参考图**：`data/pilot/02-doodle.jpg` 随每次请求一起发送（data URL），锁定角色造型和画风。
- **参数**：1920×1080，无水印。模型默认 `doubao-seedream-5-0-pro-260628`，可用 `--image-model doubao-seedream-5-0-flash-260915` 换成更快更便宜的 flash，或在工作目录的 `video.json` 里写 `image_model`。只影响这次新出的图。
- **分层镜头**每层单独出图：背景层用 `style` + 图层描述 + 「不要画人物」；人物、道具、蒸汽用只含画风的 `LAYER_STYLE` + 图层描述 + 「画在纯色底上」，合成时按底色抠掉。`glow` 不出额外的图。
- 图存在 `img/sNN.jpg`，图层存在 `layers/sNN/<图层>.jpg`。下载链接只接受火山引擎的域名，单张不超过 20MB。

## ⑦ 配音怎么生成

和出图同时进行（默认 4 路并发）。**一个镜头一段配音**：把这个镜头的几行旁白拼起来，发给豆包 seed-audio。

- 音色是复刻音色 `S_VQ18Xi2i2`，语速 10，提示词要求「语速平稳，沉稳、克制，像深夜的旁白」并「一字不差地说」。
- 送去配音前：`HOMOPHONES` 里读错的多音字换成同音字（比如单独的 `ta` → 他），去掉「」（它会让时间戳错位），“” 换成 ‘’。**字幕仍然用原文**。
- 接口返回音频和**词级时间戳**，存成 `voice/sNN.mp3` 和 `voice/sNN.mp3.json`，元数据里记着当时的旁白原文。
- 旁白改过以后，对应镜头的旧配音会被拒绝，不会悄悄重新计费；确认要重配就删掉 `voice/<id>.mp3` 再跑。

## ⑧ 时间线与字幕

拿到所有配音后，纯计算、不调接口：

1. **逐字对齐**：把词级时间戳摊到每个字上，并和旁白逐字比对；对不上说明配音过期，直接报错，避免字幕整体错位。
2. **裁停顿**：配音模型有时在句间留好几秒空白。句末停顿最多保留 0.6 秒，逗号和词间 0.35 秒，超出的从中间裁掉；镜头之间插 0.55 秒，片头 0.4 秒，片尾 1.8 秒。拼成一条 `voice.wav`，响度统一到 -23 LUFS。
3. **镜头切点**：每个镜头的起点就是它第一句话开始的时刻，换算成 30fps 的帧号。
4. **字幕**：先在标点处断开（引号、书名号跟着前一条）；超过 16 字的，用 jieba 分词在词与词之间均匀切开，避开介词、副词后面和助词前面这些别扭的切点，也不切进引号和书名号里。每条字幕从它第一个字开始、到最后一个字结束，间隔太短的首尾相接，避免闪烁。写出 `subs.ass`（烧进画面）和 `subs.srt`（上传平台时作为可关闭的字幕轨）。

## ⑨ 渲染成动画

每个镜头渲染成一段片段（默认 6 路并发），帧数由时间线决定：

- **普通镜头**：整张图做缓慢、轻微的透视摆动（四个角各按不同周期正弦漂移），让静态图「呼吸」起来。
- **分层镜头**：按 `motion` 配方把各层合成成动画（风景掠过、道具缩放、蒸汽升起、区域发光）。
- **质感**：轻微色差、柔化、饱和度、暗角，加逐帧胶片颗粒。静态镜头先把质感烘进图里，逐帧只算摆动、颗粒和字幕，快约 20%。
- **烧字幕**：按片段在整条时间线上的位置烧入，所以单独重渲一段也和全片对得上。

然后把所有片段用 `-c copy` 无损拼接，混入配音和 BGM（芒果花蜜，-21 dB，淡入 2 秒、淡出 3 秒），输出 `data/videos/<slug>/<slug>.mp4`（H.264 1920×1080 30fps）。

`render.json` 记下每个镜头的帧数、编码参数和画面指纹（图片哈希 + motion + `LOOK_VERSION`），供 `reshoot` 和英文版核对。`render` 结束时打印本次出图张数和配音计费秒数。

```bash
uv run stickman video render data/videos/20261019-tui-kai           # 补齐缺的图和配音（付费），渲染、拼接、混音
uv run stickman video render data/videos/20261019-tui-kai --offline # 完全不调接口，素材不全就列出缺什么
```

- 已有的图和配音直接复用，中途失败重跑只补缺的。
- 参考图、音色、BGM、并发数等默认值在 `src/stickman/video/config.py`；单条视频可在工作目录放 `video.json` 覆盖（只接受已知参数）。

## 检查与返工（reshoot）

渲染完看成片或联系表，挑出问题镜头（画面里出现乱码文字、人物变形、蒸汽没对准杯口等）：

```bash
uv run stickman video reshoot data/videos/20261019-tui-kai s02 s07          # 重新出图，只重渲这几段
uv run stickman video reshoot data/videos/20261019-tui-kai s03:fg           # 分层镜头只重出一层
uv run stickman video reshoot data/videos/20261019-tui-kai s11 --no-generate  # 改了 motion 参数，只重渲不出图
```

- 时间线和编码参数必须和上次 `render` 一致，否则要求整条重新 `render`。
- 换下来的旧图移到 `img_rejected/<id>.<n>.jpg`，不删除。
- 读错的多音字：加到 `src/stickman/video/text.py` 的 `HOMOPHONES`，删掉那个镜头的配音再 `render`。

### 分层镜头

默认每个镜头是一张图整体摆动。只有画面里**有一样东西按自己的逻辑运动、和人物无关，而且这个运动正是这句旁白要表达的**，才在 `shots.json` 里加 `motion`。分层镜头最多占一半，`check` 会拦；两人拥抱这类互相交叠的画面、特写、纯符号画面不分层。

```json
{"id": "s03", "line": 5, "bg": "橙色", "scene": "两人坐火车",
 "motion": {"type": "scroll", "speed": 700,
            "layers": {"bg": "车厢内部，左边一扇大窗户", "scenery": "田野、远山和电线杆", "fg": "两个火柴人并排坐在座椅上"}}}
```

| type | 图层 | 动起来的是 | 参数 |
|---|---|---|---|
| `scroll` | bg（窗户填绿）、scenery、fg | 窗外风景循环掠过，人物随车颠簸 | `speed` 像素/秒，默认 700 |
| `scale` | bg、prop（道具）、fg（人物） | 道具以人物脚下为中心从 `from` 缓到 `to`（画面宽度比例），人物缩到 1/3 画面高并轻微呼吸 | `from`、`to`；`feet` 脚的位置，默认 `[0.5, 0.79]` |
| `rise` | bg、puff（一缕蒸汽） | 两缕蒸汽交替从 `at` 升起淡出 | `at` 画面比例 `[x, y]` |
| `glow` | 无（用整幅图） | 图里某种颜色的区域明暗呼吸 | `color` `#RRGGBB` |

- 人物、道具、蒸汽画在纯色底上，合成时抠掉，位置按实际画出来的范围算。
- `rise` 的 `at` 要和背景里锅、杯子的位置对上，出图后不对就改 `at` 再 `reshoot --no-generate`。
- 图层按文件名缓存：改了某个镜头的 `type` 或图层描述，要对这个镜头跑一次 `reshoot`。

## ⑩ 英文版（YouTube 全球观众）

同一个工作目录再出一条英文成片：英文配音 + 英文字幕，**图片、分镜和画面效果和中文版逐镜头一致**。

```bash
/translate-script data/videos/20261014-dan-xin                       # Claude 意译出 narration.en.txt，人审过再往下走
uv run stickman video check data/videos/20261014-dan-xin --lang en    # 译文必须一行对应中文一行，不能留汉字
uv run stickman video render data/videos/20261014-dan-xin --lang en   # 只配英文音（付费），不出图 → <slug>.en.mp4
uv run stickman video reshoot data/videos/20261014-dan-xin s03 --lang en --no-generate
```

- 翻译一行对应一行（镜头按行号切），意译成自然的英文口语，引用换成核实过的英文原文。
- 英文配音用平台音色 `en_male_hades_uranus_bigtts`，语速 5（中文的 10 套到英文上太赶），可用 `video.json` 的 `speaker_en` 或 `--speaker` 覆盖。词的时间摊到字母上，裁停顿和字幕对齐沿用中文的逻辑；字幕按单词折行，每行最多 42 个字符。
- 中文是主版本：英文版缺图会报错；中文版必须先用当前的图、motion 和画面效果渲染过，否则英文版会列出要先重渲的中文镜头。中文版重出了图，再对英文版 `reshoot --no-generate` 同一个镜头。
- 英文版的产物带 `.en` / `_en`：`narration.en.txt`、`voice_en/`、`clips_en/`、`subs.en.ass`、`subs.en.srt`、`render.en.json`、`<slug>.en.mp4`。
- 译文改过的行，删掉对应的 `voice_en/<id>.mp3` 再渲染。

## ⑪ 发布信息：标题、简介、封面

```bash
/package-video data/videos/20261019-tui-kai          # Claude 挑封面镜头、写标题、简介、出处和封面字 → publish.json
uv run stickman video package data/videos/20261019-tui-kai             # → title.txt、description.txt、thumbnail.jpg、thumbnail-3x4.jpg
/package-video data/videos/20261014-dan-xin en       # 英文版 → publish.en.json
uv run stickman video package data/videos/20261014-dan-xin --lang en   # → title.en.txt、description.en.txt、thumbnail.en.jpg
```

`publish.json` 由 Claude 按 `.claude/skills/package-video` 写，命令只做校验和生成：

```json
{"title": "越怕失去一个人，越容易把ta推开",
 "description": "ta两个小时没回消息……（两三句）",
 "sources": [{"author": "罗伯特·默顿", "title": "社会理论与社会结构", "kind": "book", "detail": "1968",
              "note": "《自我实现的预言》（1948）一文收在此书中", "link": ""}],
 "thumbnail": {"shot": "s09", "text": "越怕失去\n**越推开**"}}
```

- **简介** = 正文 + 自动排版的「本期出处」。出处优先写成图书（文章收在哪本书里就写那本书），其次是论文、访谈、演讲、文章（`kind`：book / paper / interview / lecture / article）。`link` 留给以后的商品链接，填了就在出处下面多一行「链接：…」。
- **出处防编造**：中文版每条出处的 `author` 必须和草稿 front matter 里的作者一字不差（默认草稿 `drafts/<目录名>.md`，可用 `--draft` 指定）；英文版的出处条数要和中文版一一对应。草稿 locator 里没有的版本信息，要先派 `source-hunter` 核实。
- **平台限制**：标题最多 100 个字符；中文简介连同出处最多 1000 字（抖音），英文最多 5000 字节（YouTube）；都不能有 `<` `>`。
- **封面**：中文出 16:9（YouTube、抖音横屏）和 3:4 竖版（抖音），英文只出 16:9。
  - 从所选镜头的整幅图里取背景主色和人物范围，在同色画布上把人物放大摆到一侧（竖版在下方），另一侧排大字。分层镜头没有整幅图，不能做封面，`glow` 镜头可以。
  - 字用得意黑（本身是倾斜粗体），整体再旋转 6–7°，每行左右错开；白字配深色描边和同色系的立体厚度，`**整行**` 包住的强调行用背景色的互补色（灰背景用暖黄）并且更大。
  - 封面字一到三行，每行中文最好不超过 6 个字，英文用大写短语；字号按文字框自动算，字越少越大。
  - 画面最后加和视频一致的轻微色差、暗角和颗粒。

## 目录结构

```
src/stickman/collect/bilibili/   # 采集与转写
src/stickman/writing/            # 文案：读取、分析校验、出处库、查重
src/stickman/video/              # 视频：分镜、出图、配音、时间线、字幕、合成
.claude/skills/                  # analyze-script / write-script / translate-script / package-video
.claude/agents/source-hunter.md  # 引用核实 agent
docs/style-guide.md              # 写稿硬性约束
analysis/  library/              # 分析结果和出处库（入 git）
drafts/                          # 草稿
data/                            # 采集数据、视频工作目录和成片（不入 git）

data/videos/<slug>/
  narration.txt  shots.json  video.json(可选)
  img/  layers/  img_rejected/   # 图
  voice/  voice_trim/  voice.wav # 配音
  subs.ass  subs.srt             # 字幕
  clips/  render.json  timings.json
  publish.json  title.txt  description.txt  thumbnail.jpg  thumbnail-3x4.jpg   # 发布信息
  <slug>.mp4                     # 成片（英文版另有 *.en.* / *_en/）
```

## 开发

```bash
uv run pytest --cov=stickman --cov-report=term-missing
```

单元测试用替身代替 yt-dlp、ffmpeg、Whisper 和付费接口，不访问网络，覆盖率保持 80% 以上。开发流程和约定见 `CLAUDE.md`。
