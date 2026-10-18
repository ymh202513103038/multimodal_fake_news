# 路线图与待办（第 3 步之后）

当前状态：**第 0~2 步已落地；第 3 步脚本已就绪（mock 验证通过，待真实 key 实测）；第 5 步抽样工具已就绪**。

> Windows 控制台若中文日志乱码：运行前执行 `$env:PYTHONIOENCODING='utf-8'`
> （Git Bash：`export PYTHONIOENCODING=utf-8`）。数据文件本身是 UTF-8，不受影响。

## 第 3 步：YouTube（脚本已就绪，待真实 key 验证）

`prototype/step3_youtube_probe.py` 已实现，mock 模式可跑：
`python step3_youtube_probe.py --mock --query "fake news" --limit 10 --fresh`

- `search.list` 每次调用消耗 **100** 配额；默认日配额 10000 → **每天最多 ~100 次搜索**；
- 先只拿 `video_id + snippet`，**不下载**；单次 `limit=10` 做链路验证；
- 配额账本已内置：`metadata/quota_ledger.jsonl` 记每次调用；当日累计 + 本次预估
  超过 `--quota-cap`（默认 8000）直接拒跑；
- key：`--api-key` 或环境变量 `$env:YT_API_KEY`（不要写进代码/仓库）；
- 媒体：yt-dlp 按 video_id 下载字幕/缩略图/音频
  （`--write-auto-subs --write-thumbnail --extract-audio`），不走 API 配额；
  完整命令见 step3 脚本头部注释；
- 输出 `metadata/youtube_meta.jsonl`，以 `video_id` 关联（与 X 的 tweet_id 平行）；
- 真实模式前置：设置代理环境变量（见下文「网络提示」）。

### 每日收割器（已就绪，待 key 实测）

`prototype/harvest_daily.py`：每天用免费配额轮转关键词扩数据集。

- 词表 `prototype/youtube_keywords.txt`（15 个种子词，可随时增删）；
- 默认 10 词/天 ≈ 1010 units（每个词 ≈ 101 units：search 100 + details 1）；
- cursor 断点在 `metadata/harvest_state.json`，多天覆盖全部词表后循环；
- 结果按 video_id 幂等去重；`--quota-cap 8000` 双保险；
- 定时任务（key 实测通过后创建）：
  `schtasks /Create /TN "FakeNews-YT-Harvest" /TR "C:\Users\10996\Documents\deepseek-harness\default-workspace\multimodal_fake_news\run_harvest.bat" /SC DAILY /ST 09:30 /F`
  删除：`schtasks /Delete /TN "FakeNews-YT-Harvest" /F`；手动触发：`schtasks /Run /TN "FakeNews-YT-Harvest"`；
  任务日志：`logs/harvest_task.log`。
- 注意：googleapis 走 API 也要代理——python 进程需设 `HTTPS_PROXY` 环境变量；
  计划任务场景可在 `run_harvest.bat` 里加一行 `set HTTPS_PROXY=http://127.0.0.1:7897`（实测直连失败时再加）。

## 第 3.5 步：YouTube 媒体落盘（已就绪，实测通过）

`prototype/step4_youtube_media.py`：把 youtube_meta.jsonl 候选变成多模态本体。
零 API 配额（yt-dlp 直连视频 CDN）；媒体本体在 media/（不入库），清单 `metadata/youtube_media.jsonl` 入库。

- 三种模式：`--what subs`（字幕 vtt + 缩略图 + info.json，默认最轻）、`audio`（m4a 音轨）、
  `video`（≤720p 视频+音轨合并 mp4——自动检测 venv 内置 imageio-ffmpeg 的 ffmpeg，
  无则退回 360p 预合并）；已实测 720p 合并成品（AV1 视频 + Opus 音轨）；
- 幂等：清单按 (video_id, what) upsert；yt-dlp archive 防重下；小批量 + 随机间隔（礼貌下载）；
- 字幕限流对策：YouTube timedtext 对代理 IP 常 429 → 字幕"尽力而为"，
  以目录内有无 .vtt 判定 subs_ok；失败条目下次运行自动重试；
  已实测：en 字幕可获取，zh-Hans 翻译字幕常被限流；可靠替代 = audio 模式 + Whisper 转写；
- 用法：`$env:HTTPS_PROXY='http://127.0.0.1:7897'` 后
  `python prototype/step4_youtube_media.py --what subs --limit 5`；
- 待办：抽帧（fps≈1，ffmpeg 已随 imageio-ffmpeg 就绪，写个封装即可）；zh-Hans 翻译字幕被限流时依赖 en 字幕或 ASR。

## 第 4 步：最小调度 Agent（未开始）

- 任务队列 `prototype/tasks.json`：`{id, source, keyword, limit, status, retries, created_at}`；
- 路由：`source == "x"` → step1+step2；`source == "youtube"` → 第 3 步脚本；
- 限流：429/超时 → 指数退避（30s/60s/120s）；连续 3 次失败 → `status=paused` 并写日志；
- 验收：提交 X（20 条）+ YouTube（10 条）两个任务，自动依次跑完。

## 第 5 步：数据集制作（抽样工具已就绪）

- 清洗：过滤损坏文件、空文本、纯广告（低文本量 + 高链接比）；
- 抽样：`prototype/make_label_sheet.py` 从 meta jsonl 抽 N 条生成
  `metadata/label_sheet.csv`（列即 labeling_rules.md 的 schema，可复现、追加去重）；
- 标注：按 [labeling_rules.md](labeling_rules.md) 双人标 20~30 条，算 kappa；
- 划分：mini train/val（例如 16/4），先验证下游流程，不急全量。

## 第 6 步（二期）：chrome-mcp 新闻网站

- 真浏览器接管 + 自动滚动，优先中文辟谣平台与新闻站；
- 先做 1 个站、1 个栏目，手动验证选择器后再自动化。

## 采集账号备忘

```bash
twscrape add_account <用户名> <密码> <邮箱>:<邮箱密码>
twscrape accounts
```

- 用**备用小号**；X 对自动化有条款限制，采集前确认条款；
- 异常（401/429）立即停，别硬刚；备用号轮换前先看 [compliance_checklist.md](compliance_checklist.md)。

## 网络提示（2026-10-17 本机实测）

- 本机**直连 Google 全部超时**（www.googleapis.com / console.cloud.google.com 均返回 000）；
- 系统里配置了本地代理 `127.0.0.1:7897`（Clash 类），**走代理可达**：googleapis 返回 400（无效 key 的正常报错）、console 返回 302（登录页）；
- Chrome 走系统代理，直接打开 console.cloud.google.com 正常；
- **命令行/Python 默认不走系统代理**，调用前设置：

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'
$env:HTTP_PROXY='http://127.0.0.1:7897'
```

  `requests` / `curl` 会自动读取这两个环境变量；用完可 `Remove-Item Env:HTTPS_PROXY` 撤掉。
