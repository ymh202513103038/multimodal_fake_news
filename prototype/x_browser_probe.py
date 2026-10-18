r"""X 真浏览器采集（chrome-mcp 版 step1）——不依赖 twscrape 账号。

原理：驱动你已登录 X 的 Chrome，打开搜索页，滚动加载，页内 JS 抽取推文结构化数据。

用法：
  python x_browser_probe.py --keyword "fake news" --rounds 3 --limit 20
  python x_browser_probe.py --keyword "谣言" --rounds 2

输出：metadata/twitter_raw.jsonl（mode=browser，与 step1 同 schema）
前置：Chrome 已登录 x.com；mcp-chrome 扩展已 Connect（chrome_mcp.py 可用）。
合规：仅公开内容、小批量、低频滚动；凭据零接触（不碰 cookie/密码）。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import quote

from chrome_mcp import McpClient
from common import METADATA_DIR, append_jsonl, existing_ids, setup_logger

log = setup_logger("x_browser")
OUT_DIR = METADATA_DIR / "x"   # 语义化命名：{关键词slug}__{日期}.jsonl


def keyword_slug(kw: str) -> str:
    """关键词 -> 文件名安全的 slug（中文等非 ASCII 会退化为 x，建议英文关键词）。"""
    s = re.sub(r"[^a-z0-9]+", "_", kw.lower()).strip("_")
    return s or "x"


def known_ids_everywhere() -> set:
    """跨所有历史采集文件去重。"""
    ids: set = set()
    if OUT_DIR.exists():
        for f in OUT_DIR.glob("*.jsonl"):
            ids |= existing_ids(f)
    return ids

# 页内抽取：每条推文的文本/时间/链接/作者/图片/视频标记
# 注意：桥接会拦截含 URL 查询串的结果（隐私过滤），所有 URL 必须先剥掉 ? 后内容
JS_EXTRACT = """
const stripQ = u => String(u).split('?')[0];
const cleanText = s => String(s).replace(/https?:\\/\\/\\S+\\?\\S+/g, m => m.split('?')[0]);
return JSON.stringify(
  [...document.querySelectorAll('article[data-testid="tweet"]')].map(a => ({
    text: cleanText(a.querySelector('[data-testid="tweetText"]')?.innerText || ''),
    time: a.querySelector('time')?.getAttribute('datetime') || '',
    link: stripQ(a.querySelector('a[href*="/status/"]')?.href || ''),
    user: a.querySelector('div[data-testid="User-Name"]')?.innerText.split('\\n')[0] || '',
    imgs: [...a.querySelectorAll('img[src*="pbs.twimg.com/media"]')].map(i => stripQ(i.src)),
    hasVideo: !!a.querySelector('[data-testid="videoPlayer"]')
  }))
);
"""
JS_SCROLL = "window.scrollBy(0, 3200); await new Promise(r => setTimeout(r, 1800)); return 'scrolled';"


def tweet_id_from_link(link: str) -> str:
    m = re.search(r"/status/(\d+)", link or "")
    return m.group(1) if m else ""


def main() -> None:
    ap = argparse.ArgumentParser(description="X 真浏览器采集（chrome-mcp）")
    ap.add_argument("--keyword", default="fake news")
    ap.add_argument("--rounds", type=int, default=3, help="滚动抽取轮数（每轮约 5 秒）")
    ap.add_argument("--limit", type=int, default=20, help="本次最多入库条数")
    a = ap.parse_args()

    query = a.keyword  # 纯关键词；X 的 filter: 语法在本通道会返回空结果，媒体在抽取后标记
    url = f"https://x.com/search?q={quote(query)}&f=live"

    cli = McpClient()
    cli.ensure()
    log.info("打开搜索页：%s", url)
    cli.call("chrome_navigate", {"url": url})
    time.sleep(6)  # 等待首屏加载

    raw: dict[str, dict] = {}  # link -> tweet（页内去重）
    for rd in range(1, a.rounds + 1):
        r = cli.call("chrome_javascript", {"code": JS_EXTRACT})
        content = (r.get("result", {}) or {}).get("content", [])
        text = content[0].get("text", "{}") if content else "{}"
        # 工具返回信封：{"success":..,"result":"<内层JSON字符串>"}
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            log.warning("第 %d 轮返回不是合法 JSON（可能被桥接拦截或未登录），提前结束", rd)
            break
        inner = envelope.get("result") if isinstance(envelope, dict) else envelope
        if isinstance(inner, str):
            try:
                tweets = json.loads(inner)
            except json.JSONDecodeError:
                log.warning("第 %d 轮内层结果不是合法 JSON：%s", rd, str(inner)[:120])
                break
        elif isinstance(inner, list):
            tweets = inner
        else:
            log.warning("第 %d 轮结果结构异常：%s", rd, str(inner)[:120])
            break
        for t in tweets:
            if isinstance(t, dict) and t.get("link"):
                raw[t["link"]] = t
        log.info("第 %d/%d 轮：页面可见推文 %d 条，累计不同 %d 条",
                 rd, a.rounds, len(tweets), len(raw))
        if rd < a.rounds:
            cli.call("chrome_javascript", {"code": JS_SCROLL})
            time.sleep(1)

    # 规范化为 step1 schema
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for t in raw.values():
        tid = tweet_id_from_link(t.get("link", ""))
        if not tid:
            continue
        media = [{"type": "photo", "url": u, "ext": "jpg"} for u in t.get("imgs", [])]
        if t.get("hasVideo"):
            media.append({"type": "video", "url": t["link"], "ext": "mp4"})
        records.append({
            "tweet_id": tid,
            "created_at": t.get("time") or None,
            "text": t.get("text", ""),
            "lang": None,
            "user": {"id": None, "username": t.get("user", "")},
            "post_url": f"https://x.com/i/web/status/{tid}",
            "media": media,
            "source_query": f"{query} [browser]",
            "mode": "browser",
            "collected_at": now,
        })

    # 语义化命名：metadata/x/{关键词slug}__{日期}.jsonl
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"{keyword_slug(query)}__{date.today().isoformat()}.jsonl"
    known = known_ids_everywhere()
    new = [r for r in records if r["tweet_id"] not in known][: a.limit]
    append_jsonl(out_file, new)
    with_media = sum(1 for r in new if r["media"])
    log.info("抽取 %d 条，跨文件去重后新增 %d 条（含媒体 %d 条，上限 %d）-> %s",
             len(records), len(new), with_media, a.limit, out_file)


if __name__ == "__main__":
    main()
