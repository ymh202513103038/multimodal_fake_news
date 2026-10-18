# 多模态虚假新闻数据集（试点）

「采集 → 数据集制作 → 标注 → 评测」最小闭环。**仅学术研究，不商用。**

## 试点一期范围（详见 docs/requirements.md）

- 数据源：X/Twitter（twscrape）+ YouTube（Data API v3 + yt-dlp）；中文源与新闻网站放二期
- 模态：文本 + 图片 + 短视频
- 标注：帖子级人工标注 + 事实核查证据对齐（借 GroundLie360 的 evidence 思想）
- 合规：只采公开内容、脱敏、小批量，每阶段过 docs/compliance_checklist.md

## 目录

```
docs/            需求、合规清单、标注规范、路线图、全景采集方案（参考）
prototype/       可运行链路：step1 X元数据 → step2 下载媒体 → step3 YouTube元数据
                 → make_label_sheet 生成标注表 → validate 抽检
metadata/        采集产物与标注表（jsonl/csv，入库；quota_ledger.jsonl 为配额账本）
media/           媒体文件（不入库，见 .gitignore）
fake_news_env/   Python 虚拟环境（不入库）
```

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
- X：先加 twscrape 账号（见 docs/roadmap.md），去掉 `--mock`；
- YouTube：先一次性 `setx YT_API_KEY "你的key"`（永久，key 不进代码/仓库，重开终端生效），
  再 `python step3_youtube_probe.py --query "fake news" --limit 10`；
- 视频媒体一律走 yt-dlp（不耗 API 配额），命令见 step3 脚本头部注释。

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
- 定时：Windows 计划任务每天调 `run_harvest.bat`（见 docs/roadmap.md 第 3 步）。

## Git / GitHub 约定

- 本仓库以本地 git 管理；远端建议 GitHub **私有**仓库（采集脚本与合规敏感，暂不公开）
- 永不入库：`fake_news_env/`、`media/`、`accounts.sqlite`、`accounts.txt`、`.env`（已配置 .gitignore）
- 环境重建：`python -m venv fake_news_env` → `pip install -r requirements.txt`
- 推送远端：`git remote add origin <私有仓库地址>` → `git push -u origin main`
