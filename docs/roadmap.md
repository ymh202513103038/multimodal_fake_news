# 路线图与待办（第 3 步之后）

当前状态：**第 0~2 步已落地**，mock 模式端到端跑通（见 `prototype/`）。

> Windows 控制台若中文日志乱码：运行前执行 `$env:PYTHONIOENCODING='utf-8'`
> （Git Bash：`export PYTHONIOENCODING=utf-8`）。数据文件本身是 UTF-8，不受影响。

## 第 3 步：YouTube（未开始）

- `search.list` 每次调用消耗 **100** 配额；默认日配额 10000 → **每天最多 ~100 次搜索**；
- 先只拿 `video_id + snippet`，**不下载**；单次 `limit=10` 做链路验证；
- 媒体：yt-dlp 按 video_id 下载字幕/缩略图/音频
  （`--write-auto-subs --write-thumbnail --extract-audio`），不走 API 配额；
- 输出 `metadata/youtube_meta.jsonl`，以 `video_id` 关联；
- 配额预算写日志：每次调用记 `quota_units`，当日累计接近 8000 时停。

## 第 4 步：最小调度 Agent（未开始）

- 任务队列 `prototype/tasks.json`：`{id, source, keyword, limit, status, retries, created_at}`；
- 路由：`source == "x"` → step1+step2；`source == "youtube"` → 第 3 步脚本；
- 限流：429/超时 → 指数退避（30s/60s/120s）；连续 3 次失败 → `status=paused` 并写日志；
- 验收：提交 X（20 条）+ YouTube（10 条）两个任务，自动依次跑完。

## 第 5 步：数据集制作（未开始）

- 清洗：过滤损坏文件、空文本、纯广告（低文本量 + 高链接比）；
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
