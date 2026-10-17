# bili-collect

B 站 up 主内容采集工具：列出视频、抓取元数据，按需下载视频、抽取音频，并用本地 Whisper 转写成带时间戳的文案。仅用于个人调研（研究选题和文案结构），不要搬运或重新上传他人作品。

## 环境

- macOS + Apple Silicon（转写使用 mlx-whisper）
- [uv](https://docs.astral.sh/uv/)、ffmpeg（`brew install ffmpeg`）
- 第一次转写会下载 `mlx-community/whisper-large-v3-turbo` 模型（约 1.6GB）

```bash
uv sync
```

## 用法

统一入口是 `stickman`，采集命令在 `stickman bili` 下；`bili-collect` 是它的别名，两种写法等价（下面的示例用 `bili-collect`）。

```bash
# 列出 up 主视频（会补全标题、时长、日期，并写入索引）
uv run bili-collect list https://space.bilibili.com/3546974439607171 --limit 20

# 处理单个视频：元数据 + 转写
uv run bili-collect fetch BV1CSWXzAEys --transcribe

# 同时下载视频文件 / 只抽音频
uv run bili-collect fetch BV1CSWXzAEys --video
uv run bili-collect fetch BV1CSWXzAEys --audio

# 批量采集，可断点续跑：已完成的会跳过，单条失败不影响其他视频
uv run bili-collect collect 3546974439607171 --limit 30 --transcribe
```

`fetch` 和 `collect` 都支持 `--video` / `--audio` / `--transcribe`，可以任意组合。
`--cookies-from-browser`、`--data-dir`、`--sleep` 等全局选项必须写在子命令**之前**，例如 `bili-collect --sleep 2 collect ...`。用 `stickman` 入口时写在 `bili` 之后：`stickman bili --sleep 2 collect ...`。

### 文案从哪来

`--transcribe` 用 **Whisper** 生成主文案（有标点、一句一行，适合阅读和分析）。登录时还会**另存 B 站官方字幕**作为对照：

- 官方字幕保存为 `subtitles.<语言>.srt`，比如 `subtitles.ai-zh.srt`（B 站 AI 字幕）或 `subtitles.zh-CN.srt`（人工字幕）
- 需要登录：带上 `--cookies-from-browser chrome`。不带 cookies 或视频没有字幕时，只是没有这份对照文件，不影响主文案
- 官方字幕没有标点，并且按屏幕显示长度断句，常在句子中间断开，所以不作为主文案。适合用来核对人名
- 不需要对照时加全局选项 `--no-subtitles`，可以少发一次请求
- `transcript.info.json` 记录主文案用的是哪个 Whisper 模型
- 想用新规则重新生成某篇文案：删掉它目录下的 `transcript.json` 再跑一次
- Whisper 结果会自动去掉片尾音乐段的幻觉句（如「请不吝点赞 订阅 转发…」），并纠正常见人名错写（叔本华、齐泽克、克尔凯郭尔等，见 `transcribe.CORRECTIONS`）

### 批量速度

`collect` 采用流水线：下载在后台线程进行，Whisper 在主线程逐个转写，下载时间被转写时间覆盖（实测约快 20%）。
`--jobs N`（1–4，默认 1）控制同时访问 B 站的任务数。瓶颈在 Whisper，一般不需要调大；Whisper 始终一次只跑一个——在 M 系列芯片上实测并发转写反而更慢。

支持的输入：BV 号、视频链接（含 `?spm_id_from=...` 等参数）、b23.tv 短链、mid、空间链接。

### 被风控拦截（-352 / 412）

B 站对未登录的列表请求会间歇性拦截。用已登录 B 站的浏览器 cookies 重试：

```bash
uv run bili-collect --cookies-from-browser chrome list 3546974439607171
```

第一次读取 Chrome cookies 时，macOS 会请求钥匙串访问权限。cookies 只在本地使用，不会写入任何文件。也可以用 `--cookies cookies.txt` 指定 Netscape 格式的文件（已在 `.gitignore` 中忽略）。

全局选项（放在子命令前）：`--data-dir`（默认 `data`）、`--sleep`（请求间隔秒数，默认 1.0）、`--model`（Whisper 模型）、`--no-subtitles`、`-v`。

## 输出结构

```
data/<mid>/videos.jsonl          # 视频索引：bvid、title、duration、view_count、upload_date
data/<mid>/<bvid>/meta.json      # 元数据：简介、标签、播放、点赞等
data/<mid>/<bvid>/source.m4a     # 下载的音频流
data/<mid>/<bvid>/audio.wav      # 16kHz 单声道
data/<mid>/<bvid>/video.mp4      # 仅 --video
data/<mid>/<bvid>/transcript.txt # [mm:ss] 一句一行
data/<mid>/<bvid>/transcript.srt
data/<mid>/<bvid>/transcript.json
data/<mid>/<bvid>/transcript.info.json  # 主文案用的 Whisper 模型
data/<mid>/<bvid>/subtitles.<语言>.srt  # B 站官方字幕对照（需登录，可能不存在）
```

## 文案分析与创作

在 Claude Code 里用两个 skill 完成，Python 命令负责读数据、校验和查重：

```bash
/analyze-script BV1CSWXzAEys     # 拆解结构、列出引用，并行派 source-hunter 核实出处，保存到 analysis/
/analyze-script pending          # 处理所有还没分析的文案
/write-script 分手后总想弄明白为什么   # 按 docs/style-guide.md 和出处库写原创草稿到 drafts/，并自动查重
```

对应的命令行工具：

```bash
uv run stickman corpus list|show <BV>|export --output out/corpus.md   # 读已采集的文案
uv run stickman analysis list|show <BV>|save <file>                  # 分析结果（保存时校验格式）
uv run stickman library build|show                                    # 出处库 library/sources.jsonl
uv run stickman draft check drafts/xxx.md                             # 篇幅统计 + 和已采集文案查重
```

- 引用判定：原话 / 意译 / 误引 / 无法核实 / 未注明出处。草稿只能引用「原话」和「意译」。
- 查重：去掉标点后，连续 15 字以上和任一已采集文案相同就报警（退出码 1）。60 字以内、成对的中文引号里的直接引用不计入。没有可比较的文案时退出码为 2。
- `analysis/`、`library/`、`drafts/` 是我们自己的工作成果，纳入 git；`data/` 不入库。

## 做视频

定稿的草稿 → 1–3 分钟的火柴人视频。出图用火山方舟 Seedream，配音用豆包 seed-audio（复刻音色），剪辑用本机 ffmpeg。

```bash
uv run stickman video init drafts/20261014-jie-jing.md       # 生成 data/videos/<slug>/：narration.txt + 待填写的 shots.json
# 在 Claude Code 里按每条「> 画面」填好 shots.json 的 bg / scene
uv run stickman video check data/videos/20261014-jie-jing     # 校验分镜；没填完的不会去调付费接口
uv run stickman video render data/videos/20261014-jie-jing    # 补齐缺的图和配音（付费），渲染、拼接、混 BGM
uv run stickman video reshoot data/videos/20261014-jie-jing s02 s07   # 只重做这几张图和这几段
```

- API key 只从 macOS 钥匙串读：`ark-api-key`（出图）、`volc-tts-key`（配音）。
- 已有的 `img/sNN.jpg`、`voice/sNN.mp3(.json)` 直接复用：中途失败重跑只补缺的；`render --offline` 完全不调接口。
- 旁白改过以后，对应镜头的旧配音会被拒绝（不会自动重新配音计费），删掉 `voice/<id>.mp3` 再跑。
- `reshoot` 要求时间线和编码参数和上次 `render` 一致（记在 `render.json`），旧图移到 `img_rejected/`；`--no-generate` 只重渲不出图。
- 参考图、音色、BGM 等默认值在 `src/stickman/video/config.py`，单条视频可在工作目录放 `video.json` 覆盖（只接受已知参数）。
- 多音字在 `src/stickman/video/text.py` 的 `HOMOPHONES` 里用同音字替换，只影响配音，字幕保留原文。

### 分层镜头

默认每个镜头是一张图整体缓慢摆动。环境需要「自己动」的镜头（窗外风景掠过、圈子缩小、热气升起）可以在 `shots.json` 里加 `motion`，拆成几层分别出图再合成。分层镜头最多占全片镜头数的一半，`check` 会拦。

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

- 图层存在 `layers/<镜头>/<图层>.jpg`；人物、道具、蒸汽画在纯色底上，合成时抠掉，位置按实际画出来的范围算。
- `reshoot <dir> s03` 重出这个镜头的所有图层，`reshoot <dir> s03:fg` 只重出一层（旧图移到 `img_rejected/s03-fg.<n>.jpg`）；改了 `speed` / `at` 等参数用 `--no-generate` 只重渲。
- 图层按文件名缓存：改了某个镜头的 `type` 或图层描述，旧图层不会自动作废，要对这个镜头跑一次 `reshoot`。

### 英文版（YouTube 全球观众）

同一个工作目录里再出一条英文成片：英文配音（平台音色 `en_male_hades_uranus_bigtts`，语速 `speech_rate_en` 默认 5，比中文的 10 慢）+ 英文字幕，图片和分镜共用中文版。

```bash
/translate-script data/videos/20261014-dan-xin                      # 在 Claude Code 里意译出 narration.en.txt，人审过再往下走
uv run stickman video check data/videos/20261014-dan-xin --lang en   # 译文必须一行对应中文一行，不能留汉字
uv run stickman video render data/videos/20261014-dan-xin --lang en  # 只配英文音（付费），不出图 → 20261014-dan-xin.en.mp4
uv run stickman video reshoot data/videos/20261014-dan-xin s03 --lang en --no-generate
```

- 英文版的产物都带 `.en` / `_en`：`narration.en.txt`、`voice_en/`、`clips_en/`、`subs.en.ass`、`render.en.json`、`<slug>.en.mp4`。中文版文件名不变。
- 图归中文版：英文版缺图会报错（先渲染中文版），`reshoot --lang en` 只能加 `--no-generate`。中文版重出了图，再对英文版 `reshoot --no-generate` 同一个镜头。
- 每次渲染都会写 `subs.srt` / `subs.en.srt`，上传 YouTube 时可以作为字幕轨（可搜索、可关闭），和烧录字幕互不影响。
- 英文配音按词返回时间戳，流水线把每个词的时间摊到它的字母上，停顿裁剪和字幕对齐沿用中文的逻辑。字幕按标点断开，过长的按单词折成长度相近的几行（每行最多 42 个字符）。
- 音色可在 `video.json` 里用 `speaker_en` 覆盖，或者 `--lang en --speaker <id>`。

## 开发

```bash
uv run pytest --cov=stickman --cov-report=term-missing
```

单元测试用替身代替 yt-dlp、ffmpeg 和 Whisper，不访问网络。
