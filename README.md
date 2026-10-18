# 多模态虚假新闻数据集（试点）

「采集 → 数据集制作 → 标注 → 评测」最小闭环。**仅学术研究，不商用。**

## 试点一期范围（详见 documentation/requirements.md）

- 数据源：X/Twitter（twscrape）+ YouTube（Data API v3 + yt-dlp）；中文源与新闻网站放二期
- 模态：文本 + 图片 + 短视频
- 标注：帖子级人工标注 + 事实核查证据对齐（借 GroundLie360 的 evidence 思想）
- 合规：只采公开内容、脱敏、小批量，每阶段过 documentation/compliance_checklist.md

## 目录（两层结构：原始采集层 → 案例数据集层）

```
documentation/            需求、合规清单、标注规范(v0.2 GroundLie360)、路线图、全景采集方案（参考）
skills/          沉淀的操作技能：label-groundlie360（三级标注 SOP）等
prototype/       采集与组装链路：
                 step1 X元数据(twscrape) → step2 下载媒体 → step3 YouTube元数据
                 → step4 YouTube媒体落盘 → x_browser_probe X真浏览器采集(chrome-mcp)
                 → news_probe 新闻/核查站采集 → make_cases 案例组装
                 → make_label_sheet 标注表 → validate 抽检
                 辅助：chrome_mcp 直连客户端 / diag_x、test_x_search、link_diag 诊断
metadata/        原始层（jsonl/csv，全部入库）：
                 ├── youtube_meta.jsonl / youtube_media.jsonl   YouTube 元数据与媒体清单
                 ├── x/{关键词}__{日期}.jsonl                    X 真浏览器采集（语义化命名）
                 ├── news/{站点}/{站点}__{日期}.jsonl            核查站列表，每站独立文件夹（piyao、snopes）
                 ├── label_sheet.csv                             标注表（v0.2 schema）
                 ├── case_names.json                             案例语义化命名映射
                 └── quota_ledger.jsonl / harvest_state.json     配额账本 / 收割断点
media/           原始层：媒体本体（不入库，见 .gitignore）
dataset/         数据集层：案例制（98 案例 = YT 47 + X 21 + 核查源 30）；文本入库，媒体本体仅本地
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
文本类文件（正文/字幕/理由/来源）入库；媒体本体仅本地。
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

## Git / GitHub 约定

- 本仓库以本地 git 管理；远端建议 GitHub **私有**仓库（采集脚本与合规敏感，暂不公开）
- 永不入库：`fake_news_env/`、`media/`、`accounts.sqlite`、`accounts.txt`、`.env`（已配置 .gitignore）
- 环境重建：`python -m venv fake_news_env` → `pip install -r requirements.txt`
- 推送远端：`git remote add origin <私有仓库地址>` → `git push -u origin main`
