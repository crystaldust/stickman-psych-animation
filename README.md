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

`fetch` 和 `collect` 都支持 `--video` / `--audio` / `--transcribe`，可以任意组合（`--transcribe` 已包含抽音频）。
`--cookies-from-browser`、`--data-dir`、`--sleep` 等全局选项必须写在子命令**之前**，例如 `bili-collect --sleep 2 collect ...`。

支持的输入：BV 号、视频链接（含 `?spm_id_from=...` 等参数）、b23.tv 短链、mid、空间链接。

### 被风控拦截（-352 / 412）

B 站对未登录的列表请求会间歇性拦截。用已登录 B 站的浏览器 cookies 重试：

```bash
uv run bili-collect --cookies-from-browser chrome list 3546974439607171
```

第一次读取 Chrome cookies 时，macOS 会请求钥匙串访问权限。cookies 只在本地使用，不会写入任何文件。也可以用 `--cookies cookies.txt` 指定 Netscape 格式的文件（已在 `.gitignore` 中忽略）。

全局选项（放在子命令前）：`--data-dir`（默认 `data`）、`--sleep`（请求间隔秒数，默认 1.0）、`--model`（Whisper 模型）、`-v`。

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
```

## 开发

```bash
uv run pytest --cov=bili_collect --cov-report=term-missing
```

单元测试用替身代替 yt-dlp、ffmpeg 和 Whisper，不访问网络。
