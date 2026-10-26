# -*- coding: utf-8 -*-
"""补抓 factcheck 案例正文（piyao + snopes）。

背景：dataset/factcheck__* 案例最初只存了标题行（piyao）或列表页卡片摘要
（snopes，以 "... Read More" 截断），reason.txt 是占位符。本脚本补抓文章
正文写入 text/text.txt，修正 source.txt（含 piyao URL 缺斜杠 bug），
并清掉 reason.txt 占位符。

用法（在仓库根目录）：
  python prototype/patch_factcheck_bodies.py --dry-run   # 预览，不写盘
  python prototype/patch_factcheck_bodies.py             # 实际抓取
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
INDEX = DATASET / "index.json"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fix_piyao_url(url: str) -> str:
    """修复 piyao URL 缺斜杠 bug：piyao.org.cn2026... -> piyao.org.cn/2026..."""
    return re.sub(r"(piyao\.org\.cn)(?=\d)", r"\1/", url)


def read_source_url(case_dir: Path):
    src = case_dir / "web" / "source.txt"
    if not src.exists():
        return None
    m = re.search(r"source_url:\s*(\S+)", src.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def fetch_piyao(url: str):
    r = requests.get(url, timeout=20, headers=UA)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one(".content") or soup.select_one("article")
    if not el:
        raise ValueError("未找到 .content 正文容器")
    title_el = soup.select_one("h1") or soup.select_one(".title")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    body = el.get_text("\n", strip=True)
    # 压掉连续空行
    body = re.sub(r"\n{3,}", "\n\n", body)
    return title, body


def fetch_snopes(url: str):
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one("article")
    if not el:
        raise ValueError("未找到 article 正文容器")
    # 去掉导航/分享等噪音节点
    for noise in el.select("nav, .share, .related, script, style, aside"):
        noise.decompose()
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    body = el.get_text("\n", strip=True)
    body = re.sub(r"\n{3,}", "\n\n", body)
    # 抓裁决词（Rating: 下一行）
    rating = None
    m = re.search(r"Rating:\s*\n\s*(\S+)", body)
    if m:
        rating = m.group(1)
    return title, body, rating


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    targets = [c for c in idx["cases"] if c.get("platform") in ("piyao", "snopes")]
    print(f"待处理 factcheck 案例: {len(targets)} 条 (dry_run={args.dry_run})")

    ok, fail = 0, []
    for c in targets:
        cid = c["id"]
        case_dir = DATASET / c["case_dir"]
        platform = c["platform"]
        try:
            url = read_source_url(case_dir)
            if not url:
                raise ValueError("source.txt 无 URL")
            if platform == "piyao":
                fixed = fix_piyao_url(url)
                title, body = fetch_piyao(fixed)
                rating = None
                out_url = fixed
            else:
                title, body, rating = fetch_snopes(url)
                out_url = url

            new_text = f"# {title}\n\n{body}\n"
            print(f"[{cid}] {platform} 正文 {len(body)} 字"
                  + (f", 裁决={rating}" if rating else ""))
            if not args.dry_run:
                (case_dir / "text" / "text.txt").write_text(new_text, encoding="utf-8")
                (case_dir / "web" / "source.txt").write_text(
                    f"source_url: {out_url}\n"
                    f"retrieved_at: {datetime.now(timezone.utc).isoformat()}\n",
                    encoding="utf-8")
                # 清掉占位符（reason 留给人工标注）
                reason_f = case_dir / "reason" / "reason.txt"
                if reason_f.exists() and "占位" in reason_f.read_text(encoding="utf-8"):
                    reason_f.write_text("", encoding="utf-8")
            ok += 1
        except Exception as e:
            fail.append((cid, f"{type(e).__name__}: {e}"))
            print(f"[{cid}] FAIL {type(e).__name__}: {e}")
        time.sleep(1.5)  # 礼貌间隔

    print(f"\n完成: 成功 {ok}, 失败 {len(fail)}")
    for cid, err in fail:
        print(f"  - {cid}: {err}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
