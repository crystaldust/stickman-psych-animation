# 抖音 / YouTube 自动上传调研

*2026-10-15 | 来源约 30 个（官方文档优先）| 置信度：YouTube 高，抖音中（创作者侧官方规格大多查不到）*

## 结论

| | YouTube | 抖音 |
|---|---|---|
| 官方上传 API | 有：Data API v3 `videos.insert`，官方 Python 客户端 | 有：`upload_video` + `create_video`，但**只有企业主体**能建所需的移动/网站应用，个人号拿不到 |
| 能否全自动 | 能（OAuth 一次授权，refresh token 长期有效） | 个人号只能走网页自动化（Playwright 驱动 creator.douyin.com），有风控和发短信验证码的情况，非完全无人值守 |
| 现在的 16:9 片子 | 按长视频处理（Shorts 只认竖屏/方形 ≤3 分钟） | 可以发，但平台内容以竖屏为主；横屏的推荐表现没有可靠数据 |
| 推荐尺寸 | 长视频 16:9 1920×1080（或更高）；Shorts 9:16 1080×1920 | 9:16 1080×1920 是事实标准（官方只写「720p 以上」） |
| 必须做的声明 | 风格化动画 + 自己的复刻声音**不需要**声明 AI；`selfDeclaredMadeForKids=false` | **法定必须**声明 AI 生成（《人工智能生成合成内容标识办法》第 10 条，2025-09-01 起） |
| 最大风险 | 「不真实内容」政策（模板化量产不能变现）；YPP 不对中国大陆居民开放 | 「恶意营销号」「AI 起号」治理；商用 BGM 版权；鸡汤同质化 |

## 1. YouTube

### API

- `videos.insert` + `google-api-python-client` + `google-auth-oauthlib`（`InstalledAppFlow.run_local_server`，scope `youtube.upload`），可续传上传，失败按指数退避重试。官方示例是 Python 2 + oauth2client，只借鉴上传/重试逻辑。([上传指南](https://developers.google.com/youtube/v3/guides/uploading_a_video))
- **配额已变**：2025-12-04 起上传从约 1600 单位降到约 100；2026-06-01 起上传有独立配额桶，每天 100 次、每次 1 单位。其它调用共享每天 10,000 单位：`thumbnails.set` 约 50、`captions.insert` 400、`videos.update` 50。([revision history](https://developers.google.com/youtube/v3/revision_history)、[videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert))
- **未审核项目的视频是否会被锁成私密：官方文档自相矛盾。**
  - videos.insert 页（2026-10-08 更新）写「不再限制为私密」。
  - revision history 里 2020-07-28 的规则（未审核项目上传后锁私密）没有被撤销的记录，2026 年的第三方教程也还说会锁。
  - **需要实测一次**：用 `unlisted` 上传，看返回的状态。审核和提额走 [Audit and Quota Extension Form](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)。
- OAuth 同意屏幕停在「Testing」时，refresh token **7 天过期**；要发布成「In production」才能长期用。([OAuth 2.0](https://developers.google.com/identity/protocols/oauth2))
- 可在 API 里设置的字段：
  - 标题、说明、标签：标题 ≤100 字符；说明 ≤5000 **字节**，中文约 1600 字；标签合计 ≤500 字符。
  - 分类、默认语言和音频语言；`localizations` 放多语言标题和说明。
  - `status.publishAt` 定时发布，需配 `private`。不写 `privacyStatus` 默认公开。
  - `status.containsSyntheticMedia`（AI 声明，2024-10 起）、`selfDeclaredMadeForKids`。
  - 来源：[videos resource](https://developers.google.com/youtube/v3/docs/videos)
- 缩略图 `thumbnails.set` 需要账号已验证（[72431](https://support.google.com/youtube/answer/72431)）。字幕轨 `captions.insert` 要自带时间轴（[captions.insert](https://developers.google.com/youtube/v3/docs/captions/insert)）。

### 尺寸与编码（[官方推荐编码设置](https://support.google.com/youtube/answer/1722171)）

- 容器：MP4，moov 前置（faststart）。
- 视频：H.264 High、逐行、4:2:0、BT.709、原生帧率，1080p30 SDR 约 8 Mbps（1440p 16、4K 35–45）。
- 音频：AAC-LC 48 kHz 立体声 384 kbps。
- 缩略图：16:9，现推荐 3840×2160（最小宽 640），JPG/PNG，≤50 MB。「1280×720、<2 MB」是旧说法。
- Shorts：方形或竖屏、≤3 分钟（2024-10-15 起）；16:9 一律算长视频。超过 1 分钟的 Short 只要有版权声明就会全球屏蔽。([15424877](https://support.google.com/youtube/answer/15424877))
- **对照现在的成片**：`ffprobe` 显示我们输出 H.264 High 1920×1080 30fps，但视频只有 **4 Mbps**、音频 **AAC 192k / 44.1 kHz**，代码里也没有 `+faststart`。与官方推荐差的是码率、采样率和 faststart。

### 内容注意事项

- **AI 声明**：只有「看起来真实」的内容才要声明（真人、真实事件、写实场景）。动画、奇幻明确不需要；用自己复刻的声音配旁白也被列为不需要声明。([14328491](https://support.google.com/youtube/answer/14328491))
- **不真实内容政策**（2025-07-15 由 repetitious 改名）：「大量生产、通用、重复」「看起来是模板做的」内容不能变现，AI 内容同样适用。每条视频要有不同的文案、画面和结构。**这是 AI 流水线频道变现的主要风险。**([1311392](https://support.google.com/youtube/answer/1311392))
- **YPP**：1000 订阅 + 12 个月 4000 小时观看（或 90 天 1000 万 Shorts 播放），要开两步验证、绑 AdSense。**可用地区列表有香港、台湾，没有中国大陆**，大陆居民变现需要另行解决合规身份问题。([72851](https://support.google.com/youtube/answer/72851)、[7101720](https://support.google.com/youtube/answer/7101720))
- **商用 BGM**：Content ID 声明可能导致屏蔽或被分走收益。官方页没说买了授权是否就不会被声明，要问曲库方有没有白名单流程。YouTube 音频库的曲子保证不会被声明，但只能在 Studio 手动下载。([6013276](https://support.google.com/youtube/answer/6013276)、[3376882](https://support.google.com/youtube/answer/3376882))
- **面向儿童**：卡通形象不等于面向儿童，但必须自己声明。心理/情感题材每次显式设 `false`。([9528076](https://support.google.com/youtube/answer/9528076))
- **中文观众**：`defaultLanguage=zh-Hans` + 英文 `localizations`。烧录字幕不能被搜索，可以另外上传 SRT 字幕轨（流水线已有字幕文件）。
- **从大陆上传**：没有官方指引。OAuth 和上传请求都要走代理；用续传 + 重试。`client_secret.json` 和 token 不入 git。

### 未查实

- 未审核项目的私密锁到底还在不在。
- API 加 `#Shorts` 会不会影响分类。
- 高分辨率上传是否能获得 VP9 等更好的编码（坊间说法，无官方来源）。
- 商用授权能否避免 Content ID 声明。

## 2. 抖音

### API

- 接口：
  - `POST open.douyin.com/api/douyin/v1/video/upload_video/` → `video_id`，再 `.../create_video/` 发布。
  - scope `video.create.bind`，用户 OAuth 授权，按 `open_id` 发。
  - 应用要在「能力实验室」申请「代替用户发布内容到抖音」。
  - 来源：[上传视频](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video)、[创建视频](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create)
- 接口限制：
  - 时长 ≤15 分钟；文件 ≤4 GB，>50 MB 建议分片、>300 MB 必须分片；MP4/WebM，720p 以上。
  - 文案（含 #话题、@）≤1000 字；封面可指定帧或上传图片；可见范围、是否允许下载可设。
  - 每用户每应用每天 ≤75 条；发布后先审核，审核通过前仅自己可见。
  - 没有定时发布参数，也没有 AIGC 声明参数。
- **个人号拿不到**：开发者入驻页写明个人主体只能建小程序、小游戏、直播小玩法；发布接口需要的「移动应用 / 网站应用」只对有营业执照的企业开放。([入驻](https://developer.open-douyin.com/docs/resource/zh-CN/developer/join/join-into-developer-platform/)、[创建应用](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/app-mgmt/create-mobile-and-web-app))
- 官方 SDK 只有 Java、Go、Node.js，**没有 Python**。([SDK 概览](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/sdk-overview)，语言列表来自搜索摘要)
- H5「分享到抖音」也要注册应用，而且最终要人在 App 里点发布，不是无人值守。([open_ticket](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/tools-ability/jsb-management/open-ticket))

### 非官方途径

- [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)（MIT，约 1.5 万星）：
  - 用 Playwright 驱动 creator.douyin.com，cookie 登录，支持定时发布。
  - 发布时触发短信验证码要人工输入。
  - 项目正在重构（2026-03），准备换「更难被检测」的驱动，说明平台确实在识别自动化。
- 风险：
  - 页面改版就失效。
  - 社区自律公约里关于第三方工具的条款没有查到原文，封号概率没有数据。
  - 2025-04《「恶意营销号」治理规范》针对批量、雷同发布，引入 100 分「账号健康分」，分低限流、限发布、限变现。([IT之家](https://www.ithome.com/0/849/480.htm))

### 尺寸

- 官方只给出接口的「720p 以上、≤15 分钟、≤4 GB」。帧率、码率、封面尺寸、标题和话题数量限制都没有查到官方数字。
- 平台供给以竖屏为主（2025 年 1–8 月竖屏短剧约 4 万部、横屏 325 部，[人人都是产品经理](https://www.woshipm.com/operate/6328838.html)，未交叉验证）。没有查到横屏会被降权的官方说法或可靠数据。
- 1080×1920 是行业惯例，但查到的出处都是讲 TikTok 的。
- 网页端创作者平台可以定时发布（第三方视频显示约一周窗口，未核实）。

### 内容注意事项

- **AI 标识是法律义务**（《人工智能生成合成内容标识办法》，2025-09-01 施行，[网信办](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm)）：
  - 第 10 条：发布者要主动声明，并使用平台的标识功能，不得删除或隐藏标识。
  - 第 5–6 条：文件元数据里的隐式标识会被平台读取。Seedream、seed-audio 的输出可能已带隐式标识（推测，未核实）。不声明但被检出时，平台会标「疑似使用了 AI 生成技术」。
  - 抖音入口：发布页「高级设置 → 自主声明 → 内容由 AI 生成」。([网易](https://www.163.com/dy/article/K8CP37UE0511CPVM.html)、[每经](https://www.nbd.com.cn/articles/2025-09-08/4052360.html))
  - **自动化脚本必须包含勾选这一步。**
- **复刻声音**：2026-04 的整治针对未经授权克隆**他人**声音（[界面](https://www.jiemian.com/article/14302758.html)）。克隆自己的声音没有专门规定，但仍属合成音频，要一并声明 AI。
- **商用 BGM**：没有查到 2025–26 年关于视频内嵌音乐的现行规则。
  - 抖音曲库是平台授权的；接口也支持传 `music_id` 从曲库挂音乐。
  - 稳妥做法（推断）：发抖音的版本去掉内嵌 BGM，改挂曲库音乐；或者确认《芒果花蜜》的授权覆盖抖音。
- **AI 账号治理**：2025-05「AI 起号」专项打击 AI 生成的低俗、猎奇内容，AI 虚拟「专家」给建议，以及引导规避标识（[澎湃](https://www.thepaper.cn/newsDetail_forward_30841176)）。2024 年整治过批量雷同的「心灵鸡汤」（[IT之家](https://www.ithome.com/0/849/586.htm)）。心理/情感题材要避免鸡汤腔和同质化（推断）。
- **心理类资质**：没有查到心理健康专门规则。法律类（2025-11）、财经类（2025-12）已有自律公约，要求专业服务类内容有资质（[北青](https://news.bjd.com.cn/2025/11/25/11426394.shtml)、[财联社](https://www.cls.cn/detail/2230799)），心理类可能跟进。我们现有的「不做诊断」规则要继续守住。

### 未查实

- creator.douyin.com 的官方上传规格、标题和话题限制、封面尺寸。
- 公约中关于自动化工具的条款。
- 「代替用户发布」能力的审批标准。
- 不声明 AI 的具体处罚。
- 商用音乐的现行规则。
- 心理类内容规则。
- 新号限制。

## 方法

- 两个子任务并行调研（YouTube、抖音），共约 70 次搜索和抓取。
- 优先官方文档（developers.google.com、support.google.com、developer.open-douyin.com、cac.gov.cn），其次新闻报道。
- 只有一个来源支持的说法已标注「未核实」或「推断」。
- 抓取失败：chinalawtranslate（403）、一个 skill 页面（410）。
