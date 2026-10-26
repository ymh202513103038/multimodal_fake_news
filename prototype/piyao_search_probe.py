# -*- coding: utf-8 -*-
"""中国互联网联合辟谣平台 搜索采集（piyao.org.cn 背后的 so.news.cn 接口）。

接口：GET https://so.news.cn/xhtvapp/rumourSearch
  参数: title=关键词, pageNum, pageSize, sort=-2(时间倒序),
        timeInterval/startTime/endTime/typeName 可选过滤
  返回: {code, content: {resultList[], totalCount, pageTotal}}
  每条: id/title/url/publishTime/typeDesc/ext3(来源)/ext8(封面)/content(HTML 正文)

type 字段经验：31=食药卫生案例稿 36=全国网络辟谣(视频栏目) 37=滚动/动态稿(多为
宣传活动，非谣言案例，默认排除)。正文字段 content 已是完整 HTML，无需再抓文章页。

用法（仓库根目录）：
  python prototype/piyao_search_probe.py --keyword "维C" --pages 1 --page-size 10
  python prototype/piyao_search_probe.py --keyword "艾滋病" --pages 3 --types 31,36
输出: metadata/piyao/{keyword}__{日期}.jsonl（按文章 id 幂等去重，追加写入）
"""
import argparse
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "metadata" / "piyao"
API = "https://so.news.cn/xhtvapp/rumourSearch"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA, "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.piyao.org.cn", "Referer": "https://www.piyao.org.cn/",
}


def html_to_text(html: str) -> str:
    """正文 HTML -> 纯文本（保留段落换行与图片/视频 URL 行）"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        img.replace_with(f"\n[图] {src}\n")
    for vid in soup.find_all("video"):
        src = vid.get("src", "")
        vid.replace_with(f"\n[视频] {src}\n")
    text = soup.get_text("\n")
    text = html_mod.unescape(text)
    text = text.replace("\u2002", " ").replace("\u2026", "…")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def search(keyword: str, page: int, page_size: int, retry: int = 3):
    params = {"title": keyword, "pageNum": page, "pageSize": page_size,
              "timeInterval": "", "startTime": "", "endTime": "",
              "typeName": "", "sort": "-2"}
    for attempt in range(1, retry + 1):
        try:
            r = requests.get(API, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            if "<!DOCTYPE" in r.text[:200] or "attack.jinxibei" in r.text[:2000]:
                raise RuntimeError("WAF 拦截页(可能触发风控，稍后再试)")
            d = r.json()
            if d.get("code") != 200:
                raise RuntimeError(f"code={d.get('code')} {d.get('message')}")
            return d["content"]
        except Exception as e:
            wait = attempt * 10
            print(f"  第{attempt}次失败: {e}，{wait}s 后重试")
            time.sleep(wait)
    return None


def load_existing(out_file: Path):
    seen = set()
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(json.loads(line)["id"])
                except Exception:
                    pass
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True, help="搜索关键词")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=10)
    ap.add_argument("--types", default="31,36",
                    help="保留的 type 逗号分隔；31=案例稿 36=辟谣栏目稿, "
                         "37=宣传动态(默认排除)。传 all 不过滤")
    ap.add_argument("--max", type=int, default=0, help="最多入库条数, 0=不限")
    args = ap.parse_args()

    keep_types = (None if args.types.strip().lower() == "all"
                  else {t.strip() for t in args.types.split(",")})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", args.keyword).strip("_") or "q"
    out_file = OUT_DIR / f"{slug}__{datetime.now():%Y-%m-%d}.jsonl"
    seen = load_existing(out_file)
    print(f"关键词[{args.keyword}] -> {out_file.name} (已有 {len(seen)} 条)")

    saved = skipped_type = skipped_dup = 0
    for page in range(1, args.pages + 1):
        content = search(args.keyword, page, args.page_size)
        if not content:
            print(f"  第{page}页获取失败，停止")
            break
        total, page_total = content.get("totalCount"), content.get("pageTotal")
        rows = content.get("resultList") or []
        print(f"第{page}/{page_total}页: 本页 {len(rows)} 条, 总量 {total}")
        for row in rows:
            rid = row.get("id")
            if rid in seen:
                skipped_dup += 1
                continue
            if keep_types and str(row.get("type")) not in keep_types:
                skipped_type += 1
                continue
            rec = {
                "id": rid,
                "title": row.get("title"),
                "url": row.get("url"),
                "publish_time": row.get("publishTime"),
                "type": row.get("type"),
                "type_desc": row.get("typeDesc"),
                "source": row.get("ext3") or row.get("reprint"),
                "cover": (row.get("ext8") or "").rstrip(",") or None,
                "plat_unique_id": row.get("platUniqueId"),
                "body_text": html_to_text(row.get("content") or ""),
                "keyword": args.keyword,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            with out_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            seen.add(rid)
            saved += 1
            if args.max and saved >= args.max:
                break
        if args.max and saved >= args.max:
            break
        time.sleep(1.5)

    print(f"\n完成: 新入库 {saved} 条, 类型过滤跳过 {skipped_type}, "
          f"重复跳过 {skipped_dup} -> {out_file.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
