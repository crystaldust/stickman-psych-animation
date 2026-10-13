# stickman-video

用 AI 流水线制作火柴人动画 YouTube 频道（faceless，长视频 8–15 分钟）。

## 当前状态

- 文档在 `docs/`：调研结论 `docs/research-stickman-workflow.md`，ECC 流程复盘 `docs/ecc-orchestration-retro.md`。
- 目录约定：包名 `stickman`，统一入口 `stickman <子命令>`（见 `src/stickman/cli.py` 的 `COMMANDS`）。采集在 `src/stickman/collect/<平台>/`，新功能按领域放在 `src/stickman/<领域>/`。
- 已有工具：`stickman bili`（别名 `bili-collect`，代码在 `src/stickman/collect/bilibili/`），采集 B 站 up 主的视频元数据、音视频，并用 mlx-whisper 转写文案，用法见 `README.md`。

## 命令

- 安装依赖：`uv sync`（Python 3.12，固定 3.12 是因为 mlx-whisper / numba 对 3.14 支持不稳）
- 测试：`uv run pytest --cov=stickman --cov-report=term-missing`（单元测试不访问网络，覆盖率保持 80% 以上）
- 运行：`uv run stickman --help`

## 采集（stickman bili）注意事项

- B 站对未登录请求会间歇性返回 -352 / 412 风控。遇到风控时批量任务应立即中止，不要继续请求。
- yt-dlp 会把 cookie jar 回写到 `cookiefile`，所以只能传一份临时副本给它，不能传用户的原始文件。
- 断点续跑依赖「最终产物文件名」，中间文件（`.part`、`.temp`、`.fNNN`）不能当作已完成。

## 目标流水线

```
配音.mp3
  → Whisper 生成带时间戳的文本
  → Claude 按句子生成提示词
  → Higgsfield CLI 批量出图（文件名 = 时间戳）
  → ffmpeg 或 Remotion 按时间戳拼接
  → 成片.mp4
```

人负责选题、脚本、录音；机器负责第 4–7 步（时间戳 → 出图 → 开头动画 → 剪辑）。

## 约定

- 画风：MS Paint 风格的粗糙火柴人，白色背景，16:9，每 2–3 秒换一张图。
- 素材文件名用时间戳（例如 `00-07.png` 表示 0 分 7 秒），这是自动对位的前提。
- 调用付费 API（Higgsfield、Flow 等）会消耗 credits，批量生成前先小批量试跑并报告消耗。
- 素材、成片等大文件不提交 git。

## ECC

ECC 以全局插件形式提供（v2.2.1），本项目没有做项目级安装。可用的相关 skill 有 `ecc:video-editing`、`ecc:remotion-video-creation`、`ecc:content-engine`、`ecc:market-research`、`ecc:deep-research`。技术栈定下来后，再运行 `/project-init` 按语言补装规则。
