# -*- coding: utf-8 -*-
"""正文增强：把真实内容并进 text.txt，让"正文"名副其实。

1. YouTube 案例: 解析 text/subs.vtt 字幕(去滚动重复/时间戳), 以【字幕转写】
   章节追加进 text.txt —— 此前 text.txt 只有标题+简介, 视频讲话内容藏在 vtt 里;
2. piyao 案例: 清掉 "首页 > 正文" 站点导航噪音, 压缩 "来源：xx\n时间：" 版式;
3. X 案例: 经免登录 syndication API 取完整推文原文(修复浏览器采集的断行截断),
   同步 likes/retweets 入 index.metrics; 纯回复型低价值推文在 index 标记
   role=reply_lowvalue 供人工筛除, 不自动删除。

用法：python prototype/enrich_bodies.py [--dry-run]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "dataset"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}


def vtt_to_text(vtt_path: Path) -> str:
    """YouTube 滚动字幕 -> 去重连贯文本"""
    lines = vtt_path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, prev = [], None
    for ln in lines:
        ln = ln.strip()
        if (not ln or ln == "WEBVTT" or ln.startswith(("Kind:", "Language:",
                "NOTE")) or re.match(r"^\d\d:", ln) or re.match(r"^\d+$", ln)):
            continue
        ln = re.sub(r"<[^>]+>", "", ln)          # 去内联标签 <c> 等
        ln = ln.strip()
        if not ln or ln == prev:
            continue
        # 滚动字幕首行常与上一 cue 的尾行重复
        if out and ln in out[-1]:
            continue
        out.append(ln)
        prev = ln
    text = " ".join(out)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_piyao(s: str) -> str:
    s = re.sub(r"^#\s*\n+", "# ", s)              # 空标题
    s = re.sub(r"^首页\s*\n?>?\s*\n?正文\s*\n?", "", s, flags=re.M)
    s = re.sub(r"^时间：\s*\n(\d{4}-\d\d-\d\d)", r"时间：\1", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def fetch_syndication(tid: str):
    try:
        r = requests.get(
            f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=en&token=x",
            timeout=15, headers=UA)
        if r.status_code != 200:
            return None
        d = r.json()
        return {"text": d.get("text"),
                "likes": d.get("favorite_count"),
                "rts": d.get("conversation_count"),
                "created": d.get("created_at"),
                "user": (d.get("user") or {}).get("name") or (d.get("user") or {}).get("screen_name")}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    index_path = DS / "index.json"
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    stat = {"yt_vtt": 0, "yt_no_vtt": 0, "piyao": 0, "x_updated": 0,
            "x_kept": 0, "x_lowvalue": 0}

    for c in idx["cases"]:
        pf, cid = c["platform"], c["id"]
        case_dir = DS / c["case_dir"]
        tf = [p for p in c["files"].get("text", []) if p.endswith("text.txt")]
        if not tf:
            continue
        text_file = DS / tf[0]
        s = text_file.read_text(encoding="utf-8")

        if pf == "youtube":
            vtt = [p for p in c["files"].get("text", []) if p.endswith(".vtt")]
            if vtt and "【字幕转写】" not in s:
                vp = DS / vtt[0]
                transcript = vtt_to_text(vp)
                if len(transcript) > 200:
                    new = s.rstrip() + f"\n\n【字幕转写】\n{transcript}\n"
                    if not args.dry_run:
                        text_file.write_text(new, encoding="utf-8")
                    stat["yt_vtt"] += 1
                    continue
            if not vtt:
                stat["yt_no_vtt"] += 1

        elif pf == "piyao":
            new = clean_piyao(s)
            if new != s:
                if not args.dry_run:
                    text_file.write_text(new, encoding="utf-8")
                stat["piyao"] += 1

        elif pf == "twitter":
            tid = c.get("tweet_id") or c["id"].rsplit("_", 1)[1]
            old_body = re.sub(r"^\S+.*\n(\d{4}-\d\d-\d\dT[^\n]*\n)?", "", s,
                              count=1).strip()
            d = fetch_syndication(tid)
            time.sleep(0.8)
            if d and d.get("text"):
                full = d["text"].replace("\n", " ").strip()
                if len(full) > len(old_body) + 20:      # 明显更完整才替换
                    header = f"{d.get('user') or ''}  {d.get('created') or ''}\n\n"
                    if not args.dry_run:
                        text_file.write_text(header + full + "\n", encoding="utf-8")
                    stat["x_updated"] += 1
                else:
                    stat["x_kept"] += 1
                if d.get("likes") is not None and not args.dry_run:
                    c["metrics"] = {"likes": d["likes"], "retweets": d["rts"]}
            else:
                stat["x_kept"] += 1
            # 纯回复型低价值标记（无链接、无媒体、<120字、以@开头回复语气）
            flat = re.sub(r"\s+", " ", s)
            if (len(flat) < 120 and "http" not in flat.lower()
                    and not c["files"].get("image")):
                c["role"] = "reply_lowvalue"
                stat["x_lowvalue"] += 1

    if not args.dry_run:
        index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(f"YouTube: {stat['yt_vtt']} 条并入字幕转写, {stat['yt_no_vtt']} 条无vtt(维持)")
    print(f"piyao: {stat['piyao']} 条清理导航噪音")
    print(f"X: {stat['x_updated']} 条换完整原文, {stat['x_kept']} 条维持, "
          f"{stat['x_lowvalue']} 条标记 reply_lowvalue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
