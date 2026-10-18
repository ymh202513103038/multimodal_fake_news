# 多模态虚假新闻智能体工作流 —— 数据爬取与数据集制作方案

> **范围说明**：本文是"全景参考方案"。试点一期范围以 [requirements.md](requirements.md) 为准
> （仅 X/Twitter + YouTube，英文关键词）；微博/抖音/chrome-mcp/中文核查站等属二期扩展，
> 对应 [roadmap.md](roadmap.md) 第 6 步。工具选型、命令与 Schema 设计仍以本文为准。
>
> 版本：2026-10 · 适用：多模态假新闻检测 / 谣言溯源 / 智能体（Agent）流水线的数据层

---

## 0. 总体思路：三层采集策略

```
┌─ 第 0 层：现成基准数据集（先白嫖，建立基线）
├─ 第 1 层：平台定向采集（YouTube / X / 微博 / 抖音…）
└─ 第 2 层：事实核查站 + 新闻聚合源（提供"标签锚点"与新闻全文）
```

关键设计原则：
1. **以"谣言主张（claim）"为采集主键**，而不是以平台为维度——每条数据都要能追溯到一条可核查的陈述及其核查结论（真 / 假 / 误导 / 无法核实）。
2. **多模态就地落盘**：文本 + 图片 + 视频 + 音频 + 发布上下文（时间、作者、转发链），目录结构统一。
3. **断点续爬 + 幂等**：所有任务走队列（Celery / Prefect），状态存 SQLite/Redis，失败重试不重复下载（`--download-archive`）。
4. **合规优先**：robots.txt、平台 ToS、低频限速、个人数据脱敏、版权媒体以"链接 + 缩略图"为主、仅科研用途。

---

## 1. 第 0 层：现成多模态基准数据集（建议先全量入库）

| 数据集 | 平台/语言 | 多模态内容 | 标签 | 获取 |
|---|---|---|---|---|
| **Fakeddit** | Reddit / 英文 | 标题 + 图片 | 6 类细粒度 | GitHub `ljdannull/Fakeddit-Multimodal`（注：原图片需按 ID 回拉 Reddit，已删帖较多，社区有 HF 镜像） |
| **FakeNewsNet** | PolitiFact + GossipCop / 英文 | 新闻正文 + 推文 ID + 缩略图 | 真/假 | GitHub `KaiDMML/FakeNewsNet`（社交部分需自己用 API 重爬，衰减快） |
| **SNIFFER** | GossipCop 派生 / 英文 | 新闻图 + 外部证据 | 真/假 + 解释 | GitHub `peiyi9979/SNIFFER`（ICML'24，可解释方向基线） |
| **DGM4** | 合成篡改 / 英文 | 图片-文本对齐篡改 | 4 子类（SC/SH/IR/IM） | GitHub/HF 搜 "DGM4"（AAAI'24，检测图文不匹配/替换） |
| **MMFakeBench** | 混合源 / 中英 | 图文混合误导 | 多类型 | HF（LVLM 时代新基准） |
| **MM-COVID** | 多语言多平台 | 新闻图 + 文本 | 真/假 | EMNLP'20，配套爬取脚本 |
| **Weibo 谣言集（EANN/MVAE 用）** | 微博 / 中文 | 微博文 + 配图 | 真/假 | 论文附带链接，需自行补图 |
| **MCFEND** | 中文新闻多源 | 纯文本（可作文本底座） | 真/假 | ACL'24，含 14 个中文新闻域 |
| **TAGFN** | GossipCop 变体 | 图文 | 真/假 | HuggingFace `kayzliu/TAGFN` |

**步骤：**
1. 建目录 `data/external/<dataset>/`，下载原始包 + 官方 split。
2. 写一个 `rehydrate.py`：对 Fakeddit/FakeNewsNet 缺失媒体按 ID 回拉（Reddit 用 PRAW、图片用 archive.org 反查）。
3. 统一转成 §5 的 JSONL Schema，作为后续自建数据的"金标对照"。

---

## 2. YouTube：Data API v3 + yt-dlp 组合拳

**分工：API 负责"发现与元数据"，yt-dlp 负责"重媒体下载"。**

### 2.1 YouTube Data API v3
- **开通**：Google Cloud Console → 新建项目 → 启用 *YouTube Data API v3* → 创建 API Key（公开数据）+ OAuth 客户端（评论管理/字幕）。
- **配额**：默认 **10,000 units/天**；`search.list` 一次 **100 units**，`videos.list`/`commentThreads.list` 约 **1 unit**——所以**严禁循环调 search**，正确姿势是：
  - `search.list` 只做"发现"（关键词/频道/相关视频），缓存 video_id；
  - `videos.list`（part=snippet,statistics,contentDetails,status）批量 50 个/次 取元数据；
  - `commentThreads.list` 批量拉评论（谣言传播舆情特征）。
- **配额不够**：官方 Quota Extension 表单申请提额（需项目审计），或者多 Key 多项目轮换（注意 ToS 条款）。
- **字幕**：`captions.*` 端点需要 OAuth 且只对**自己的视频**可用 → **用 yt-dlp 拉字幕**即可。
- 增量：记录 `publishedAt` 游标 + `nextPageToken`，按频道/关键词做定时任务。

### 2.2 yt-dlp（视频、音频、缩略图、字幕一键落盘）
```bash
pip install -U yt-dlp

# 元数据 + 缩略图 + 自动字幕，不下载视频（发现/入库阶段）
yt-dlp --skip-download --write-info-json --write-thumbnail \
       --write-auto-subs --sub-langs "zh.*,en.*" \
       --download-archive data/raw/youtube/archive.txt \
       -o "data/raw/youtube/%(id)s/%(id)s.%(ext)s" URL

# 需要媒体时：限高 1080p + 音频合并
yt-dlp -f "bv*[height<=1080]+ba/b" --merge-output-format mp4 \
       --limit-rate 2M --sleep-interval 3 --cookies-from-browser chrome \
       -o "data/raw/youtube/%(id)s/%(id)s.%(ext)s" URL

# 列表/频道先扁平列出，再选择性下载
yt-dlp --flat-playlist --print "%(id)s\t%(title)s" CHANNEL_URL
```
要点：
- `--download-archive` 保证幂等；`--cookies-from-browser chrome` 应对会员/年龄限制视频。
- 大批量时用 `--batch-file urls.txt` + `--concurrent-fragments`，配合代理池。
- 拿到视频后抽帧 + 语音转写：
```bash
ffmpeg -i video.mp4 -vf fps=1/5 -q:v 2 frames_%04d.jpg   # 每 5 秒一帧
whisper video.mp4 --language zh --output_format json        # ASR 文本
```

---

## 3. X/Twitter：twscrape（活跃开源首选）

仓库：`vladkens/twscrape`（Python 库 + CLI，多账号轮换、内置限速处理）。

```bash
pip install twscrape

# 1) 账号池（准备几个独享账号，别用共享号，易封）
twscrape add_accounts accounts.txt
twscrape login_accounts

# 2) Python 侧
from twscrape import AccountsPool, API
async def main():
    pool = AccountsPool(); await pool.login_all()
    api = API(pool)
    # 关键词搜索（fake news / 辟谣 相关 query）
    async for tw in api.search("谣言 OR 辟谣 lang:zh -filter:replies", limit=500):
        save(tw.json())          # tw.media 内含图片/视频信息
    # 详情、用户时间线、转发/引用链
    await api.tweet_details(tweet_id)
    await api.user_tweets(user_id, limit=100)
```
- 每账号搜索/详情有限速，twscrape 自动轮换；`accounts.txt` 建议开启 2FA 关联或用专用小号。
- **合规提示**：第三方抓取与 X 的 ToS 存在张力，仅限小规模科研，控制 QPS，勿商用分发原始数据。
- 备选：官方 API（Basic 档 \$200/月，读量有限）；历史推文可用 archive.org / Wayback 按 tweet URL 反查；图片/视频链接通常是 `pbs.twimg.com`、`video.twimg.com`，可带 cookie 直下。

---

## 4. 真浏览器接管：chrome-mcp（登录态平台兜底）

适用场景：**微博、微信公众号、抖音、B站、快手**等强登录 + 强反爬平台，或需要"看得见的滚动态"抓取热点流。

工具：`hangwin/mcp-chrome`（Chrome 扩展 + 本地 Native 服务，把你**已登录的真实 Chrome**暴露为 MCP 工具端点，供 Agent 调用）。

**步骤：**
1. Chrome 安装 mcp-chrome 扩展（GitHub Releases 或商店），装 Native Host，点 Connect。
2. 本地起 MCP 端点（默认本机回环，如 `http://127.0.0.1:<port>/mcp`，以 README 为准），把它加进你的 Agent 框架（Claude/DeepSeek/Cline 等的 MCP 配置）。
3. Agent 可用工具：取标签页/选中元素、模拟滚动加载、点击展开评论、截图、抽取 DOM 文本、下载媒体 URL。
4. 典型流程（微博热搜谣言）：
   - 打开 `weibo.com/hot/search` → 滚动采集榜单 → 对命中关键词逐条打开话题页 → 滚动评论流 → 抽取微博正文+配图+视频链接 → 媒体 URL 交给 yt-dlp 下载。
5. 注意：
   - 人工节奏滚动（随机 sleep 1–5s），单会话限量，避免触发风控/验证码；
   - 抓到的是**登录态数据，仅自用研究**，不要打包分发；
   - mcp-chrome 侧曾报告过 CORS 类安全问题（见其 issue 区）——只在可信本地环境开启，不要对着陌生网页点高危操作；
   - 留一套"人工兜底"脚本（Playwright + `--user-data-dir` 指向真实 Profile）与 mcp-chrome 互为备份。

---

## 5. 其他新闻/信息源建议（重点）

### 5.1 聚合与开放数据（免爬虫、优先用）
| 源 | 用途 | 说明 |
|---|---|---|
| **GDELT 2.0** | 全球新闻 15 分钟级更新 | DOC 2.0 API 免费查实体/主题，适合"事件级"谣言线索发现 |
| **Common Crawl CC-NEWS** | 新闻正文语料 | 每月 WARC 包，按域名过滤即可 |
| **NewsAPI.org / Event Registry / MediaCloud** | 英文新闻检索 | 免费档有配额，适合种子新闻 |
| **Wayback Machine / archive.today / URLScan.io** | **恢复被删帖** | 事实核查必需：谣言原文常被删，必须留证据链快照 |
| **RSSHub** | 万物皆 RSS | 自部署实例，路由覆盖微博热搜、知乎热榜、B站、抖音部分路由、Telegram 等 |

### 5.2 事实核查平台（标签来源，正/负样本锚点）
- 英文：Snopes、PolitiFact、FactCheck.org、AFP Fact Check（多语种）、Full Fact、EUvsDisinfo、Lead Stories。
- 中文：**中国互联网联合辟谣平台 piyao.org.cn**（国家级，结构化好爬）、腾讯较真（fact.qq.com）、微博辟谣官方账号、澎湃明查、上海辟谣平台。
- 爬取策略：栏目列表页翻页 → 详情页存 JSON（主张/结论/评级/日期/证据链接）→ 以 claim 文本为锚去社媒检索扩散样本。
- RSSHub 亦有部分核查站路由；没有路由的用 `httpx + parsel` 写常规爬虫，遵守 robots.txt 与限速（1–2 req/s）。

### 5.3 视频平台扩展
- **抖音**：反爬最强。优先 yt-dlp + Chrome cookie（`--cookies-from-browser chrome`）；批量需求可用 mcp-chrome 真浏览器方案；TikTok 同理。
- **B站**：yt-dlp 原生支持；评论用官方 Web API（`api.bilibili.com`，带 wbi 签名）。
- **微博视频/秒拍**：从 mRPC 或页面接口拿 `urls`，yt-dlp 直下。

---

## 6. 数据集制作流水线（爬 → 净 → 标 → 库）

### 6.1 统一存储 Schema（JSONL + 媒体树）
```
data/
├── raw/
│   ├── youtube/{video_id}/  video.mp4, info.json, thumb.jpg, subs/*.vtt, frames/
│   ├── x/{tweet_id}/        tweet.json, media/*
│   ├── weibo/{mid}/         post.json, images/*
│   └── factcheck/{platform}/{claim_id}.json
├── processed/
│   ├── claims.jsonl         # 主表：一条 claim 一行
│   ├── evidence.jsonl       # 扩散/传播样本
│   └── media/{sha256}.jpg|mp4
└── external/                # §1 基准数据集
```
`claims.jsonl` 字段建议：
```json
{"id":"fc-piyao-2026-0001","claim_zh":"…","claim_en":"…","verdict":"false|true|misleading|unproven",
 "factcheck":{"platform":"piyao","url":"…","published_at":"…"},
 "sources":[{"platform":"weibo","mid":"…","author_id":"…","posted_at":"…",
             "text":"…","media":[{"type":"image","sha256":"…","ocr":null}]}],
 "metrics":{"views":0,"shares":0,"comments":0},"evidence_urls":["…"]}
```

### 6.2 清洗
- 去重：文本 SimHash/MinHash + 媒体感知哈希（pHash）双层去重；
- 广告/水贴过滤：规则 + 轻量分类器；
- 语言检测（fasttext）、乱码/表情包噪声处理；
- 媒体统一转码：图片去 EXIF 但**保留拍摄时间等取证字段另存**；视频抽帧（1fps 或关键帧）。

### 6.3 标注
1. **弱监督**：核查站结论映射为标签（置信度高，占大头）；
2. **模型辅助**：用 LVLM（如 Qwen-VL / GPT-4V 类）对图文一致性、篡改痕迹打初筛分；
3. **人工复核**：Label Studio 双人标注 + 仲裁，Cohen's Kappa > 0.7 才入库；
4. **切分**：按时间切分（防泄漏），train/val/test = 7/1/2，测试集保留"未来事件"。

### 6.4 与智能体工作流衔接
```
[种子 Agent] 核查站/热搜 → 生成 claim 队列
   ↓
[扩散 Agent] X(twscrape)/YouTube(API)/微博(mcp-chrome) 检索相关帖
   ↓
[媒体 Agent] yt-dlp + ffmpeg + whisper 抽帧/转写
   ↓
[清洗 Agent] 去重/脱敏 → claims.jsonl
   ↓
[标注 Agent] 弱监督 + LVLM 初筛 → 人工 Label Studio
   ↓
[评估 Agent] 定期重跑基线模型，发现分布漂移 → 回流补采
```
任务队列建议 Prefect/Airflow + Redis；每个 Agent 有独立重试与配额账本（API units、账号限速）。

---

## 7. 合规与伦理红线

1. robots.txt / 平台 ToS 逐平台核对；X、抖音抓取存在 ToS 张力，**仅小规模科研、不商用、不分发原始数据**。
2. 版权：论文/产品中展示媒体优先用**缩略图 + 链接**；全量媒体内部留存。
3. 隐私：作者 ID 哈希化，评论区用户内容脱敏（手机号、身份证正则打码），遵循《个人信息保护法》/GDPR 最小化原则。
4. 限速与礼貌爬取：串行 1–2 req/s，夜间任务，设置全局熔断（验证码出现即停）。
5. 数据偏见：注意谣言多为突发事件，采样需覆盖健康/政治/社会/科技多领域，避免单域过拟合。

---

## 8. 落地路线图（建议 4 周）

| 周 | 目标 |
|---|---|
| W1 | 外部数据集入库（Fakeddit/FakeNewsNet/SNIFFER/DGM4/MCFEND）→ 统一 Schema → 跑通一个基线模型 |
| W2 | YouTube 管线（API 发现 + yt-dlp 落盘 + 抽帧/ASR）+ 辟谣平台爬虫（piyao、较真、Snopes） |
| W3 | twscrape 账号池搭建 + X 检索扩散样本；RSSHub 自部署覆盖微博/知乎热榜 |
| W4 | mcp-chrome 兜底强登录平台；标注平台（Label Studio）就位；全链路 Prefect 编排 + 监控 |

---

### 参考链接
- twscrape: https://github.com/vladkens/twscrape
- Fakeddit: https://github.com/ljdannull/Fakeddit-Multimodal
- FakeNewsNet: https://github.com/KaiDMML/FakeNewsNet
- mcp-chrome: https://github.com/hangwin/mcp-chrome
- YouTube 配额计算器: https://developers.google.com/youtube/v3/determine_quota_cost
- TAGFN (HF): https://huggingface.co/datasets/kayzliu/TAGFN
- 多模态虚假信息检测综述: https://www.ecice06.com/CN/10.19678/j.issn.1000-3428.0253388
