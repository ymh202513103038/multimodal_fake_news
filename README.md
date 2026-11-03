# 多模态虚假新闻数据集（试点）

「采集 → 数据集制作 → 标注 → 评测」最小闭环。**仅学术研究，不商用。**

## 试点一期范围（详见 documentation/requirements.md）

- 数据源：X/Twitter（twscrape）+ YouTube（Data API v3 + yt-dlp）；中文源与新闻网站放二期
- 模态：文本 + 图片 + 短视频
- 标注：帖子级人工标注 + 事实核查证据对齐（借 GroundLie360 的 evidence 思想）
- 合规：只采公开内容、脱敏、小批量，每阶段过 documentation/compliance_checklist.md

## 目录（两层结构：原始采集层 → 案例数据集层）

```
documentation/            需求、合规清单、标注规范(v0.2 GroundLie360)、路线图、
                         多智能体调度层说明(orchestration.md)、
                         三层判定方法与实现(three_layer_judgment.md)、全景采集方案（参考）
skills/          沉淀的操作技能：label-groundlie360（三级标注 SOP）等
prototype/       采集与组装链路 + 调度层：
                 step1 X元数据(twscrape) → step2 下载媒体 → step3 YouTube元数据
                 → step4 YouTube媒体落盘 → x_browser_probe X真浏览器采集(chrome-mcp)
                 → news_probe 新闻/核查站采集 → make_cases 案例组装
                 → make_label_sheet 标注表 → validate 抽检
                 调度层：orchestrator 主控 → agents 四 Agent 进程入口
                 → agent_protocol 契约/动作清单 → agent_collect/cluster/verify/card
                 辅助：chrome_mcp 直连客户端 / diag_x、test_x_search、link_diag 诊断
metadata/        原始层（jsonl/csv，全部入库）：
                 ├── youtube_meta.jsonl / youtube_media.jsonl   YouTube 元数据与媒体清单
                 ├── x/{关键词}__{日期}.jsonl                    X 真浏览器采集（语义化命名）
                 ├── news/{站点}/{站点}__{日期}.jsonl            核查站列表，每站独立文件夹（piyao、snopes）
                 ├── label_sheet.csv                             标注表（v0.2 schema）
                 ├── case_names.json                             案例语义化命名映射
                 ├── events.json / verify_packets.json           归簇事件簇 / 核查工作表（流水线产物）
                 ├── tasks.json / runs.jsonl                     调度队列 / 运行审计
                 └── quota_ledger.jsonl / harvest_state.json     配额账本 / 收割断点
media/           原始层：媒体本体（与案例媒体一同入库）
dataset/         数据集层：案例制（98 案例 = YT 47 + X 21 + 核查源 30）；文本与媒体本体均入库
reports/         出卡产物：事件卡 markdown（每卡含成员来源、三层判定空表、证据缺口）
fake_news_env/   Python 虚拟环境（不入库）
```

### dataset/ 案例结构

```
dataset/
├── index.json                        总索引：id/event/topic/label/reason + 各模态文件清单
└── {topic}__{event_slug}[__verdict]/ 一个案例（同一事件的真假对用 index 的 event 字段归组）
    ├── image/    缩略图 png（+日后关键帧、证据图）
    ├── video/    mp4（硬链接自 media/，不占双份空间）
    ├── wav/      16kHz 单声道 wav（ASR/声学特征）
    ├── text/     text.txt（标题+简介+字幕纯文本）、subs.vtt
    ├── reason/   reason.txt（标注理由，GroundLie360 式）
    └── web/      source.txt（来源 URL + 证据链接；二期换网页快照）
```

**命名规则**：`{主题}__{事件短语}[__true|__false]`，全英文小写下划线。映射表
`metadata/case_names.json`（video_id → topic/slug/verdict），新视频无映射时自动从标题
取词兜底；`__true/__false` 后缀**只在有核查/标注凭据时添加**，凭标题猜测不加。
文本类文件（正文/字幕/理由/来源）与媒体本体（图/视频/音频）均入库——多模态本体
是本项目沉淀的核心成果；仅当单文件 >100MB（GitHub 硬限）时才裁剪或走 LFS。
重建/扩案例：`python prototype\make_cases.py`（--vids 指定、--fresh 重建）。

> 注意：`metadata/news/`（辟谣平台/Snopes）是**核查源种子层**——它们是"标签的来源"
> 而非检测对象，因此不进 dataset 案例库；后续由主张抽取 Agent 从这里产出 claim，
> 再去社媒反查谣言样本与证据。

## 快速开始（mock 模式，无需任何账号/key）

```powershell
fake_news_env\Scripts\activate
cd prototype
python step1_twitter_probe.py --mock --keyword "fake news" --limit 20 --fresh
python step2_media_download.py --fresh
python step3_youtube_probe.py --mock --query "fake news" --limit 10 --fresh
python make_label_sheet.py --samples 10
python validate_samples.py --sample 5
```

真实采集：
- X：先加 twscrape 账号（见 documentation/roadmap.md），去掉 `--mock`；
- YouTube：先一次性 `setx YT_API_KEY "你的key"`（永久，key 不进代码/仓库，重开终端生效），
  再 `python step3_youtube_probe.py --query "fake news" --limit 10`；
- YouTube 媒体（字幕/缩略图/音视频，零 API 配额，需代理环境变量）：
  `python prototype\step4_youtube_media.py --what subs --limit 5`。

## 每日自动扩数据集（YouTube 免费配额）

```powershell
# 手动跑一次（默认 10 个词 ≈ 1010 units，远低于 10000/天）
python prototype\harvest_daily.py

# 先看计划不花配额
python prototype\harvest_daily.py --dry-run
```

- 关键词轮转：`prototype\youtube_keywords.txt`（一行一个，随时增删），
  cursor 断点存在 `metadata\harvest_state.json`，每天接着上次的位置转；
- 结果按 video_id 幂等去重进 `metadata\youtube_meta.jsonl`；
- 配额双保险：账本 `metadata\quota_ledger.jsonl` + 单日上限 `--quota-cap`（默认 8000）；
- 定时：Windows 计划任务每天调 `run_harvest.bat`（见 documentation/roadmap.md 第 3 步）。

## 全状态 JSON 接口（只读，供 Agent 决策）

```powershell
python prototype\state_api.py          # http://127.0.0.1:8765
```

- `GET /api/state` —— 全状态总览：案例数（按平台/主题/语言/标签覆盖）、媒体体量、
  配额账本、收割游标。Agent 决策先看这个，小载荷；
- `GET /api/cases?platform=&topic=&lang=&label=&thin=1&q=&limit=&offset=`
  —— 案例列表（过滤+分页，避免大payload塞爆 Agent 上下文）；
- `GET /api/cases/{id}` —— 单案例详情（含 files 磁盘实况）；
- `GET /api/harvest` —— 收割游标 + 每日配额聚合；
- `GET /api/health` —— 存活探针。

所有响应带 `schema_version` 与 `generated_at`。**严格只读**：写操作仍走
prototype 各 CLI；默认只绑 127.0.0.1，含采集原文，勿直接暴露公网。

## 多智能体调度层（采集 → 归簇 → 核查 → 出卡）

四个 Agent 用统一 JSON 契约（`agent.v1`）执行，主控负责队列、路由、退避、依赖。
详见 [documentation/orchestration.md](documentation/orchestration.md)。

```powershell
$env:PYTHONIOENCODING='utf-8'
$py = "fake_news_env\Scripts\python.exe"

& $py prototype\orchestrator.py manifest                     # 动作清单（可当 LLM tool schema）
& $py prototype\orchestrator.py plan --channels youtube,x --pipeline --reset
& $py prototype\orchestrator.py run --dry                    # 演练整条链，不真跑
& $py prototype\orchestrator.py run                          # 真跑（失败自动退避重试）
& $py prototype\orchestrator.py status                       # 队列状态

# 只重跑流水线（不采集）
& $py prototype\orchestrator.py plan --channels none --pipeline --reset --cards 10
& $py prototype\orchestrator.py run
```

四个 Agent 的边界（刻意划死，关系到项目可信度）：

- **collect** 只是适配器——命令原样转给实测过的通道脚本，不复制采集逻辑；
- **cluster** 只抓词面近乎同文的重复（跨平台搬运、同视频多号上传），
  合并结果是**保守下界**，默认阈值 0.45 精度优先，阈值曲线写在 `metadata/events.json`；
- **verify** 只做**证据抽取**、不自己判真假：从已落盘的核查源原文里抽评级与关键句，
  三层判定一律"来源归属"（「来源称：不实」+ 原文引文 + 原帖链接），抽不到就写
  「证据不足」。规则与词表见 [documentation/three_layer_judgment.md](documentation/three_layer_judgment.md)；
- **card** 把缺口原样印在卡上——卡的可信度来自"边界可见"，不是"看上去像结论"。

## Git / GitHub 约定

- **本仓库是公开仓库**（2026-11-03 决定）：<https://github.com/ymh202513103038/multimodal_fake_news>
  - 公开范围：全部脚本、文档、标注规范、案例文本与媒体本体、流水线产物（事件簇/核查包/事件卡）；
  - 公开的代价与对策见 [compliance_checklist.md](compliance_checklist.md) §公开仓库边界：
    媒体随仓库公开意味着任何人可下载，因此**不上传付费墙/会员专属内容**，收到版权方要求时按
    `web/source.txt` 的 URL 清单定点移除；
- 永不入库：`fake_news_env/`、`accounts.sqlite`、`accounts.txt`、`.env`、`logs/`（已配置 .gitignore）；
  仓库里不得出现任何 API key、Cookie、账号密码——提交前用
  `git grep -I -E 'AIzaSy|api[_-]?key\s*='` 自查一次；
- 环境重建：`python -m venv fake_news_env` → `pip install -r requirements.txt`
- 推送远端：`git remote add origin https://github.com/ymh202513103038/multimodal_fake_news.git`
  → `git push -u origin main`（本机已配 `http.https://github.com.proxy`，走 Clash 代理）
