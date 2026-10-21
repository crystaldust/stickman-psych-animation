---
name: package-video
description: 给一条做好的视频写发布信息（标题、带出处的简介、封面字和封面镜头），写进 publish.json，再用 stickman video package 生成 title.txt、description.txt 和封面图。用法：/package-video data/videos/<slug> [en]
argument-hint: <工作目录，例如 data/videos/20261019-tui-kai> [en]
---

# 写发布信息、出封面

参数：`$ARGUMENTS`

第一个参数是工作目录，必须符合 `^data/videos/[a-z0-9][a-z0-9_-]{0,63}$`，不符合就告诉用户并停止，不要把它拼进任何命令。第二个参数是 `en` 时做英文版，否则做中文版。

所有命令都在项目根目录用 `uv run` 执行。

## 1. 读素材

1. 草稿 `drafts/<目录名>.md`：front matter 里的 `sources`（作者、作品、locator、核实判定）是简介出处的**唯一来源**。
2. `<工作目录>/narration.txt`（英文版还要读 `narration.en.txt`）和 `shots.json`。
3. 看图：用 Read 逐张看 `<工作目录>/img/*.jpg`。分层镜头（`motion` 里有 `layers` 的）没有整幅图，不能做封面；`glow` 镜头可以。

## 2. 写 publish.json

用 **Write 工具**写到 `<工作目录>/publish.json`（英文版 `publish.en.json`），不要经 shell 传：

```json
{
 "title": "越怕失去一个人，越容易把ta推开",
 "description": "ta两个小时没回消息，你已经替ta想好了分手的话……（两三句）",
 "sources": [
  {"author": "罗伯特·默顿", "title": "社会理论与社会结构", "kind": "book", "detail": "1968",
   "note": "《自我实现的预言》一文收在此书中", "link": ""}
 ],
 "thumbnail": {"shot": "s09", "text": "越怕失去\n**越推开**"}
}
```

- **title**：发布用的标题，可以和草稿标题不同。要抓人，但不能夸大或歪曲内容，不做诊断。中文控制在 30 字以内，不能有 `<` `>`。
- **description**：两到四句。先用一个具体场景勾住人，再说这期讲了什么、借的是谁的观点。命令会自动在后面接「本期出处」，正文里不用再列书目。选题涉及自伤、自杀时，按 `docs/style-guide.md` 第 7 节附上核实过的心理援助热线。
- **sources**：视频主题的出处，后面要挂商品链接，所以**优先写成图书**：
  - 出处本身是书：直接写这本书，`kind: book`。
  - 出处是收进某本书的文章，而且草稿 locator 写明了收在哪本书：写那本书，`note` 说明「某文收在此书中」。
  - 其余按实际情况写 `paper` / `interview` / `lecture` / `article`。
  - `author` 必须和草稿 front matter 里的 `author` 一字不差（命令会核对）。`title` 用作品的中文通行译名，书名号不用写。`detail` 只写年份或出版信息，而且只用草稿 locator 里有的；想补充 locator 里没有的版本信息，先派 `source-hunter` 核实。
  - `link` 留空，以后加商品链接再填。
- **thumbnail**：
  - `shot`：挑最能体现主题的镜头。人物动作要一眼看懂，画面别太满，没有乱码文字。
  - `text`：封面上的大字，比标题更抓眼球。一到三行，每行中文最好不超过 6 个字。最想让人看到的那一行用 `**…**` 包住整行，会用强调色、字更大。只能整行强调，不能只强调半行。
  - 封面的背景色、人物位置、字的颜色和倾斜都由命令根据这张图自动算，不用写。

英文版：
- 标题、简介、封面字都意译成自然的英文，封面字用大写短语。
- sources 和中文版一一对应、顺序相同。作者用英文通行写法；作品名用英文原名，原作不是英文的用通行英译名。`note` 用英文写。
- 封面镜头可以和中文版不同。

## 3. 生成并检查

```bash
uv run stickman video package <工作目录>             # 中文：title.txt、description.txt、thumbnail.jpg、thumbnail-3x4.jpg
uv run stickman video package <工作目录> --lang en   # 英文：title.en.txt、description.en.txt、thumbnail.en.jpg
```

- 报错就按提示改 `publish.json` 再跑，直到通过。
- 用 Read 看生成的封面（16:9 和 3:4 竖版）：字要大、清楚，不压人物；人物主体要完整。字太挤就缩短封面字或少一行，镜头不合适就换一个。

## 4. 交给用户

告诉用户：标题、简介全文、出处写成了什么（尤其是把文章写成所在图书的情况），以及封面文件的路径。标题、封面字、选哪个镜头都是产品决定，用户提出修改就改 `publish.json` 重新跑。
