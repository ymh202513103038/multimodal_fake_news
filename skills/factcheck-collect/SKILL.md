---
name: factcheck-collect
description: 多模态虚假新闻数据集采集 SOP——X/YouTube/核查站三通道采集、常用核查源网址清单、配额治理、幂等去重、两层结构组装的完整流程，对应 multimodal_fake_news 项目的 collect 层与每日收割器。
whenToUse: 当需要为多模态虚假新闻数据集采集新样本（YouTube 搜索/媒体、X 真浏览器采集、核查站列表）、运行每日收割 harvest_daily、把新采集数据组装成 dataset 案例、或需要查常用核查源/辟谣平台网址时使用；用户提到「采集/收割/扩数据集/采一批数据/去哪个站查」时触发。
---

# factcheck-collect — 多模态虚假新闻数据集采集 SOP

为 `multimodal_fake_news` 项目采集"文本 + 图片 + 短视频"多模态样本，
落盘为原始层（metadata/ + media/）与案例层（dataset/）。**仅学术研究，只采公开内容。**

## 0. 开工准备（每次先做两件事）

**第一件：确认项目根目录。** 项目位置**不写死、每次开工先问用户**
（"项目根目录在哪？还是原来的 multimodal_fake_news 目录吗？"）。
拿到路径后先验证三样东西存在再继续：`prototype\`、`fake_news_env\`、`documentation\`。
缺 venv 就先重建：`python -m venv fake_news_env` → `pip install -r requirements.txt`。

**第二件：设置运行环境。**

```powershell
cd <项目根目录>                                  # 用刚确认的路径
$env:PYTHONIOENCODING='utf-8'                     # 中文日志不乱码
$py = "fake_news_env\Scripts\python.exe"          # 一律用 venv 里的 python
$env:HTTPS_PROXY='http://127.0.0.1:7897'          # Google/YouTube 全部要走本地代理
$env:HTTP_PROXY='http://127.0.0.1:7897'           # 命令行/Python 默认不走系统代理，必须显式设
```

**铁律（违反任何一条即停）**：

1. **只采公开内容**：不采付费墙/会员专属内容；不采手机号/邮箱/精确位置等隐私字段；
2. **配额刚性**：YouTube `search.list` 每次调用 100 units，日配额 10000；
   `metadata/quota_ledger.jsonl` 是硬约束，`--quota-cap`（默认 8000）超限直接拒跑；
3. **key 不进代码/仓库**：只用 `--api-key` 参数或 `$env:YT_API_KEY`；
   提交前自查 `git grep -I -E 'AIzaSy|api[_-]?key\s*='`；
4. **遇限流立即退避，不硬刚**：401/429 停手；X 用备用小号，账号异常立即停止；
5. **幂等**：所有采集按 video_id / tweet_id 去重，禁止重复入库；
6. **可追溯**：每条样本必须有 `source_url + collected_at`，保证可定点删除
   （版权方要求时按 `dataset/*/web/source.txt` 的 URL 清单删案例）。

## 1. 通道前置检查（先查环境再开跑，缺什么补什么）

| 通道 | 前置条件 | 缺失时的动作 |
|---|---|---|
| YouTube 元数据 | `$env:YT_API_KEY` 已设 + 代理已设 | `setx YT_API_KEY "..."` 后重开终端；代理见 §0 |
| YouTube 媒体 | 代理已设（yt-dlp 直连 CDN，**零 API 配额**） | 只设代理即可跑 |
| X 真浏览器 | Chrome 在线、已登录 x.com、mcp-chrome 扩展 Connected | 让用户开浏览器登录；不要代替用户登录 |
| 核查站（news） | 站点模板可用（站点改版会失效） | **新站先 1 条 limit 试跑验证选择器**再放量 |
| X API（twscrape） | 已加账号：`twscrape add_account <用户名> <密码> <邮箱>:<邮箱密码>` | 提醒用户用备用小号并确认条款 |

## 2. 采集目标清单（常用网址）

### 2.1 已接线站点（news_probe.py 现成，直接采）

| 站点 | 网址 | 语言 | 说明 |
|---|---|---|---|
| 中国互联网联合辟谣平台 | <https://www.piyao.org.cn/> | 中 | 官方法定辟谣平台；注意过滤专项行动通报/倡议书等版式句 |
| Snopes | <https://www.snopes.com/latest/> | 英 | 评级体系清晰（What's True/False 段落结构），解析器已适配三层映射 |

### 2.2 已验证可靠的核查站（档案/审计里实际用过的，接入需写 parse_<site>）

| 站点 | 网址 | 语言 | 强项 |
|---|---|---|---|
| AFP Fact Check | <https://factcheck.afp.com> | 英/多语 | 全球通讯社核查网，军事/地缘类覆盖好（法语版 factuel.afp.com） |
| 澎湃明查 | <https://www.thepaper.cn> | 中 | 中文国际事实核查主力栏目 |
| EUvsDisinfo | <https://euvsdisinfo.eu> | 英 | 欧盟俄宣传数据库，可按 DISINFO 条目检索，含谣言原文存档 |
| StopFake | <https://www.stopfake.org> | 英/乌 | 俄乌叙事核查，版本演变梳理细 |
| Spravdi | <https://spravdi.org> | 英/乌 | 乌克兰官方信息对抗中心 |
| Polygraph | <https://www.polygraph.info> | 英/俄 | RFE/RL 核查，俄语叙事对照好 |
| India Today Fact Check | <https://www.indiatoday.in/fact-check> | 英 | 南亚谣言，视频类核查多 |
| Factcheck Lab | <https://www.factchecklab.org> | 中 | 中文独立核查实验室 |
| GADMO | <https://gadmo.eu> | 德 | 德语区核查协作网 |
| 澎湃/搜狐"明查"镜像 | （检索"明查 + 主题"） | 中 | 同稿多站分发，引用时任选可点开的一手链接 |

### 2.3 建议补充（主流、结构化评级、学术常用；**接入前先验证可用性**）

| 站点 | 网址 | 语言 | 强项 |
|---|---|---|---|
| PolitiFact | <https://www.politifact.com> | 英 | Truth-O-Meter 六级评级，结构化最好 |
| FactCheck.org | <https://www.factcheck.org> | 英 | Annenberg 学术背景，美国政治 |
| Full Fact | <https://fullfact.org> | 英 | 英国，自动化核查研究活跃 |
| Google Fact Check Explorer | <https://toolbox.google.com/factcheck/explorer> | 多语 | **聚合检索器**——查证先用它找核查源，不是采集对象 |

### 2.4 二期规划（暂缓，先 1 站 1 栏目验证）

微博、微信公众号辟谣栏目、Facebook/Instagram 帖文——需 Chrome 登录态或新模板，
按 roadmap 待办排期；**不要为了让清单好看而仓促接入**。

### 2.5 学术对齐参考（不是采集对象，是对标基准）

Fakeddit（`github.com/ljdannull/Fakeddit-Multimodal`）、
FakeNewsNet（`github.com/KaiDMML/FakeNewsNet`）、
TAGFN（`huggingface.co/datasets/kayzliu/TAGFN`）、
GroundLie360（`github.com/yangbingjian/GroundLie360`，标注体系对齐对象）。

## 3. 三条执行路径（按需选一，先 dry 后真跑）

### 路径 A：单次试探（新通道/新关键词先走这条）

**A1. mock 链路验证（零风险，无需任何 key/账号）**：

```powershell
& $py prototype\step1_twitter_probe.py --mock --keyword "fake news" --limit 20 --fresh
& $py prototype\step2_media_download.py --fresh
& $py prototype\step3_youtube_probe.py --mock --query "fake news" --limit 10 --fresh
& $py prototype\make_label_sheet.py --samples 10
& $py prototype\validate_samples.py --sample 5
```

**A2. YouTube 真实采集（元数据 100 units/次，媒体零配额）**：

```powershell
& $py prototype\step3_youtube_probe.py --query "fake news" --limit 10   # 先 limit=10 验证
& $py prototype\step4_youtube_media.py --what subs --limit 5            # 字幕+缩略图+info.json
# --what audio → m4a 音轨；--what video → ≤720p mp4（自动用 venv 内置 ffmpeg 合并）
```

**A3. X 真浏览器采集**：

```powershell
& $py prototype\x_browser_probe.py --keyword "fake news" --rounds 4 --limit 25
# 输出 metadata/x/{关键词slug}__{日期}.jsonl；中文关键词 slug 会退化为 x，建议英文关键词
```

**A4. 核查站列表**：`news_probe.py --site piyao`（或 snopes / all）。

### 路径 B：编排链路（批量采集 + 全下游，正式扩数据时用）

```powershell
& $py prototype\orchestrator.py manifest                    # 动作清单，可当 LLM tool schema
& $py prototype\orchestrator.py plan --channels youtube,x --pipeline --reset
& $py prototype\orchestrator.py run --dry                   # 演练整链，不真跑
& $py prototype\orchestrator.py run                         # 真跑（30/60/120s 指数退避）
& $py prototype\orchestrator.py status                      # 队列状态
```

- `--channels`：youtube / yt-harvest / x / news / none（不采集）/ full（=youtube,x,news）；
- 只重跑下游不采集：`plan --channels none --pipeline --reset --cards 10`；
- 失败 3 次 → `paused`，上游 paused → 下游 `blocked`；审计在 `metadata/runs.jsonl`。

### 路径 C：每日收割（免费配额轮转扩数据集）

```powershell
& $py prototype\harvest_daily.py --dry-run    # 先看计划，不花配额
& $py prototype\harvest_daily.py              # 默认 10 词 ≈ 1010 units，远低于 10000/天
```

- 词表：`prototype/youtube_keywords.txt`（一行一个，可增删）；
- 断点：`metadata/harvest_state.json`（cursor 每天接着上次的位置轮转）；
- 结果按 video_id 幂等去重进 `metadata/youtube_meta.jsonl`；
- 双保险：`metadata/quota_ledger.jsonl` + `--quota-cap 8000`；
- 定时：`schtasks /Create /TN "FakeNews-YT-Harvest" /TR "<项目根>\run_harvest.bat" /SC DAILY /ST 09:30 /F`，
  删除 `/Delete`，手动触发 `/Run`，日志 `logs/harvest_task.log`。

## 4. 数据集结构（采来的东西落成什么样）

两层结构：**原始采集层 → 案例数据集层**。采集只写原始层；组装（§5）才进案例层。

```
<项目根>\
├── metadata\                 【原始采集层：结构化记录，全部入库】
│   ├── youtube_meta.jsonl      YouTube 元数据（video_id 幂等去重）
│   ├── youtube_media.jsonl     YouTube 媒体清单（(video_id, what) upsert）
│   ├── x\{关键词slug}__{日期}.jsonl        X 采集（tweet_id 去重）
│   ├── news\{站点}\{站点}__{日期}.jsonl    核查站列表（piyao、snopes…）
│   ├── label_sheet.csv         标注表（labeling_rules v0.2 schema）
│   ├── case_names.json         video_id → topic/slug/verdict 语义命名映射
│   ├── events.json             归簇事件簇（含阈值曲线，可复现）
│   ├── verify_packets.json     核查工作表（三层判定 + 缺口 + 检索建议）
│   ├── tasks.json / runs.jsonl 调度队列 / 运行审计
│   └── quota_ledger.jsonl / harvest_state.json   配额账本 / 收割断点
├── media\                    【原始采集层：媒体本体（mp4/m4a/vtt/缩略图）】
├── dataset\                  【案例数据集层：案例制，98+ 案例】
│   ├── index.json              总索引：id/event/topic/label/reason + 各模态文件清单
│   │                           同一事件真假对靠 event 字段归组
│   └── {topic}__{event_slug}[__true|__false]\
│       ├── image\    缩略图 png（日后扩展：关键帧、证据图）
│       ├── video\    mp4（硬链接自 media\，不占双份空间）
│       ├── wav\      16kHz 单声道 wav（ASR/声学特征）
│       ├── text\     text.txt（标题+简介+字幕纯文本）+ subs.vtt
│       ├── reason\   reason.txt（标注理由，GroundLie360 式，必非空）
│       └── web\      source.txt（来源 URL + 证据链接；定点删除的依据）
└── reports\card_<事件>.md    事件卡（成员表 + 三层判定 + 缺口清单）
```

**命名规则**：`{topic}__{event_slug}[__true|__false]`，全英文小写下划线。
`__true/__false` 后缀**只在有核查/标注凭据时添加**，凭标题猜测不加；
新视频无映射时从标题取词兜底（映射表 `metadata/case_names.json`）。

**单文件上限**：单文件 >100MB（GitHub 硬限）时裁剪或走 LFS。

## 5. 采集后组装（原始层 → 案例层）

```powershell
& $py prototype\make_cases.py                # 全量重建 dataset/
& $py prototype\make_cases.py --vids <id1>,<id2>   # 只装指定视频
& $py prototype\make_cases.py --fresh        # 重建（注意：lang 字段会丢，重建后需回填）
```

- 标签与理由自动从 label_sheet.csv 回填（`youtube:{video_id}` 行）；
- 视频硬链接复用 media/；wav 由 ffmpeg 转 16kHz；缩略图 webp→png。

## 6. 实测踩坑记录（绕开这些坑，别重新踩）

1. **googleapis 直连全部超时**——本机实测必须走 `HTTPS_PROXY=127.0.0.1:7897`；
2. **chrome-mcp 桥接有隐私过滤**：结果含 URL 查询串会被替换成 `[BLOCKED]`，
   页内抽取时要先剥 `?` 后的内容；
3. **chrome-mcp 服务端单会话**：重复 initialize 返回 500 "Already connected"，
   会话 ID 存 `logs/chrome_mcp_session.json` 复用，不要反复重建；
4. **X 搜索的 `filter:photos` 等过滤语法在本通道返回空**——用纯关键词采，
   按 media 字段事后标记；
5. **chrome-mcp 返回是双层信封** `{success, tabId, result:"<内层JSON>"}`，要两层解析；
6. **YouTube zh-Hans 翻译字幕常被限流（429），en 字幕稳定**——拿不到中文字幕时
   走 `--what audio` + Whisper 转写，不要反复重试；
7. **piyao 语料混大量专项行动通报/倡议书**——「辟谣」常只是站名/口号，
   下游判定时要过滤版式句（见 three_layer_judgment 的 PIYAO_NOISE）；
8. **piyao 文章 URL 拼接坑**：日期路径形如 `20261008/xxx/c.html`，拼接时注意保留
   `/` 分隔符，拼错会得到 `piyao.org.cn20261008/...` 这种死链；
9. **X 对自动化有条款限制**——采集前确认条款，异常（401/429）立即停，换号前先看
   compliance_checklist.md。

## 7. 验收（采集完必查）

- [ ] `metadata/*.jsonl` 有新增条目，且无重复 id（抽查 video_id/tweet_id 唯一）；
- [ ] `metadata/quota_ledger.jsonl` 记账与实际调用一致，未超 `--quota-cap`；
- [ ] 每条新样本带 `source_url + collected_at`；
- [ ] `dataset/` 新案例六件套齐全，命名符合 §4 规则；
- [ ] 合规自查：`git grep -I -E 'AIzaSy|api[_-]?key\s*=|BEGIN (RSA|OPENSSH) PRIVATE KEY'` 无命中；
- [ ] 未采到任何付费墙/会员专属内容。

## 8. 深入参考（按需读，不用背）

- 采集路线图与配额细节：项目根 `documentation/roadmap.md`
- 合规清单：`documentation/compliance_checklist.md`
- 调度层与 agent.v1 契约：`documentation/orchestration.md`
- 三层判定与站点扩展方式（新增 parse_<site>）：`documentation/three_layer_judgment.md`
- 案例结构与命名：项目根 `README.md`
