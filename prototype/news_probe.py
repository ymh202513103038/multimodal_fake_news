r"""新闻/核查站列表页探测爬虫：抓文章链接+标题，验证站点可达性与数据形状。

用法：
  python news_probe.py --site piyao     # 中国互联网联合辟谣平台（直连）
  python news_probe.py --site snopes    # Snopes 最新核查（走代理）
  python news_probe.py --site all

输出：metadata/news/{site}__{日期}.jsonl  {site, title, url, found_at}
说明：这是列表页"链接+标题"探测，正文抓取与正文解析放二期。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from common import METADATA_DIR, setup_logger

log = setup_logger("news_probe")
OUT_DIR = METADATA_DIR / "news"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) academic-research-probe/0.1"}
PROXIES = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}

SITES = {
    "piyao": {
        "url": "https://www.piyao.org.cn/",
        "proxy": False,
        # 文章链接形如 20261008/aaca2d90.../c.html（相对路径无前导斜杠，锚内常嵌套图片）
        "link_re": r'<a[^>]+href="((?:https?://www\.piyao\.org\.cn)?/?\d{8}/[0-9a-f]{16,}/c\.html)"[^>]*>(.*?)</a>',
    },
    "snopes": {
        "url": "https://www.snopes.com/latest/",
        "proxy": True,
        # 核查文章路径是 /fact-check/{slug}/（连字符）
        "link_re": r'<a[^>]+href="((?:https?://www\.snopes\.com)?/fact-check/[^"]+)"[^>]*>(.*?)</a>',
    },
}


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def absolute(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return base.rstrip("/") + href


def probe(site: str, cfg: dict, limit: int) -> list[dict]:
    proxies = PROXIES if cfg["proxy"] else None
    r = requests.get(cfg["url"], timeout=30, headers=UA, proxies=proxies)
    r.raise_for_status()
    # 无 charset 或默认 ISO-8859-1 时用内容嗅探，避免中文乱码
    if r.encoding in (None, "ISO-8859-1"):
        r.encoding = r.apparent_encoding
    html = r.text
    base = cfg["url"]
    items, seen = [], set()
    for m in re.finditer(cfg["link_re"], html, re.IGNORECASE | re.DOTALL):
        href, inner = m.group(1), m.group(2)
        title = strip_tags(inner)
        title = re.split(r"\s+Written by:", title)[0]  # 去 Snopes 署名尾噪
        if not title:  # 锚内只有图片时，尝试 title 属性或 slug
            tm = re.search(r'title="([^"]+)"', m.group(0))
            title = tm.group(1) if tm else href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        if not title or len(title) < 8:
            continue
        if re.search(r"node_\d+|/index\.htm", href):  # 栏目页/首页导航
            continue
        url = absolute(base, href)
        if url in seen:
            continue
        seen.add(url)
        items.append({"site": site, "title": title, "url": url,
                      "found_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        if len(items) >= limit:
            break
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description="新闻/核查站列表页探测")
    ap.add_argument("--site", default="all", choices=["piyao", "snopes", "all"])
    ap.add_argument("--limit", type=int, default=15, help="每个站点最多记录数")
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sites = SITES.keys() if a.site == "all" else [a.site]
    today = date.today().isoformat()

    for site in sites:
        cfg = SITES[site]
        out = OUT_DIR / f"{site}__{today}.jsonl"
        try:
            items = probe(site, cfg, a.limit)
        except Exception as e:  # noqa: BLE001
            log.error("%s 探测失败：%s", site, e)
            continue
        # 追加去重（同站同 URL 不重复）
        known = set()
        if out.exists():
            for line in out.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        known.add(json.loads(line).get("url"))
                    except Exception:  # noqa: BLE001
                        pass
        fresh = [i for i in items if i["url"] not in known]
        with open(out, "a", encoding="utf-8") as f:
            for i in fresh:
                f.write(json.dumps(i, ensure_ascii=False) + "\n")
        log.info("%s：抓到 %d 条，新增 %d 条 -> %s", site, len(items), len(fresh), out)
        for i in fresh[:3]:
            log.info("  样例：%s", i["title"][:60])


if __name__ == "__main__":
    main()
