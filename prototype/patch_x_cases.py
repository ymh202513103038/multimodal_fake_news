# -*- coding: utf-8 -*-
"""修补 X(Twitter) 案例：全 tweet_id 重命名 + 近似重复去重 + 尽力补媒体。

背景：
1. 案例目录名只保留了完整 19 位 snowflake ID 的后 10 位（tweet_7975021817），
   回溯原始推文困难，本脚本按 metadata/x/*.jsonl 的完整 tweet_id 重命名；
2. 同账号同链接的近似重复推文（如 @Majalah ICT 31 秒内两条）各占一个案例，
   保留最早一条，其余删除目录并从 index 移除；
3. 浏览器采集时 media=[] 为空，尝试免登录 syndication API 补图片；
4. 清理 reason.txt 占位符。

用法（在仓库根目录）：
  python prototype/patch_x_cases.py --dry-run   # 预览
  python prototype/patch_x_cases.py             # 实际执行
"""
import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
INDEX = DATASET / "index.json"
X_META = ROOT / "metadata" / "x"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}


def load_x_records():
    """完整 tweet_id -> 记录"""
    recs = {}
    for f in sorted(X_META.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            recs[r["tweet_id"]] = r
    return recs


def suffix_index(recs):
    """后10位 -> [完整id...]，用于反查并检测撞名"""
    m = {}
    for tid in recs:
        m.setdefault(tid[-10:], []).append(tid)
    return m


def first_link(text: str):
    # 存储文本里链接常被换行截断（https://\ndomain/...），先压掉空白再匹配
    flat = re.sub(r"\s+", "", text or "")
    m = re.search(r"https?://[^\s…]+", flat)
    if not m:
        return None
    return m.group(0).rstrip("…/").lower()


def normalize_key(rec):
    """判重键：(账号, 首链接) 优先；无链接时退化为 (账号, 归一化文本前120字)。
    只按账号判重会误杀同账号的不同新闻，因此必须带内容维度。"""
    user = ((rec.get("user") or {}).get("username") or "").strip().lower()
    link = first_link(rec.get("text", ""))
    if link:
        return (user, "link:" + link)
    flat = re.sub(r"\s+", "", rec.get("text") or "")
    return (user, "text:" + flat[:120])


def fetch_media(tid: str):
    """免登录 syndication API，返回图片 URL 列表（尽力而为）"""
    try:
        r = requests.get(
            f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=en&token=x",
            timeout=15, headers=UA)
        if r.status_code != 200:
            return [], None
        d = r.json()
        urls = []
        for ph in d.get("mediaDetails", []) or d.get("photos", []) or []:
            u = ph.get("media_url_https") or ph.get("url")
            if u:
                urls.append(u)
        return urls, d.get("text")
    except Exception:
        return [], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-media", action="store_true", help="跳过媒体下载")
    args = ap.parse_args()

    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    cases = [c for c in idx["cases"] if c.get("platform") == "twitter"]
    recs = load_x_records()
    suf = suffix_index(recs)
    print(f"twitter 案例 {len(cases)} 条, metadata 记录 {len(recs)} 条 (dry_run={args.dry_run})")

    # ---- 1) 去重：按 (账号, 首链接) 分组，保留最早 ----
    groups = {}
    for c in cases:
        m = re.search(r"tweet_(\d+)$", c["id"])
        if not m:
            print(f"[{c['id']}] 无法解析 tweet 后缀, 跳过")
            continue
        tids = suf.get(m.group(1), [])
        if not tids:
            print(f"[{c['id']}] metadata 中找不到对应记录, 跳过")
            continue
        rec = recs[tids[0]]
        groups.setdefault(normalize_key(rec), []).append((c, rec))

    drop, rename_plan = [], []
    for key, items in groups.items():
        if key == ("", ""):
            continue  # 无账号无链接的无法判重
        if len(items) > 1:
            items_sorted = sorted(items, key=lambda x: x[1].get("created_at", ""))
            keep = items_sorted[0]
            print(f"重复组 {key[0] or '(无账号)'} | {key[1] or '(无链接)'}: "
                  f"{len(items)} 条, 保留 {keep[0]['id']}")
            for c, _ in items_sorted[1:]:
                drop.append(c)
        for c, rec in items:
            if c not in drop:
                rename_plan.append((c, rec["tweet_id"]))

    # ---- 2) 重命名计划 ----
    print(f"\n将重命名 {len(rename_plan)} 条, 删除重复 {len(drop)} 条")
    for c, tid in rename_plan[:5]:
        print(f"  {c['id']}  ->  x_{c['topic']}__tweet_{tid}")
    if len(rename_plan) > 5:
        print(f"  ... 其余 {len(rename_plan)-5} 条略")

    if args.dry_run:
        print("\n(dry-run 结束，未做任何修改)")
        return 0

    # ---- 3) 执行删除 ----
    for c in drop:
        d = DATASET / c["case_dir"]
        if d.exists():
            shutil.rmtree(d)
        print(f"已删除重复案例目录: {c['case_dir']}")
    dropped_ids = {c["id"] for c in drop}

    # ---- 4) 执行重命名 + 补媒体 ----
    media_stats = {"with_media": 0, "no_media": 0}
    for c, tid in rename_plan:
        old_dir = DATASET / c["case_dir"]
        new_id = f"x_{c['topic']}__tweet_{tid}"
        new_dir = DATASET / new_id
        if old_dir.exists() and str(old_dir) != str(new_dir):
            try:
                old_dir.rename(new_dir)
            except PermissionError:
                # Windows 下目录句柄被占时 rename 拒绝访问，退化为复制+删除
                shutil.copytree(old_dir, new_dir)
                shutil.rmtree(old_dir)
        # 更新 files 路径与 id
        new_files = {}
        for modality, paths in c["files"].items():
            new_files[modality] = [p.replace(c["case_dir"], new_id) for p in paths]
        c["id"] = new_id
        c["case_dir"] = new_id
        c["files"] = new_files
        # 完整推文链接与 ID
        c["url"] = f"https://x.com/i/web/status/{tid}"
        c["tweet_id"] = tid

        # reason 占位符清理
        reason_f = new_dir / "reason" / "reason.txt"
        if reason_f.exists() and "占位" in reason_f.read_text(encoding="utf-8"):
            reason_f.write_text("", encoding="utf-8")

        # 尽力补媒体
        if not args.no_media:
            img_dir = new_dir / "image"
            if not img_dir.exists():
                urls, _ = fetch_media(tid)
                if urls:
                    img_dir.mkdir(parents=True, exist_ok=True)
                    for i, u in enumerate(urls, 1):
                        try:
                            r = requests.get(u + "?name=medium", timeout=20, headers=UA)
                            r.raise_for_status()
                            (img_dir / f"photo_{i}.jpg").write_bytes(r.content)
                        except Exception as e:
                            print(f"  [{new_id}] 图片下载失败: {e}")
                    if urls:
                        c["files"]["image"] = [f"{new_id}/image/photo_{i}.jpg"
                                               for i in range(1, len(urls) + 1)]
                        media_stats["with_media"] += 1
                    else:
                        media_stats["no_media"] += 1
                else:
                    media_stats["no_media"] += 1
                time.sleep(1.0)

    # ---- 5) 回写 index ----
    idx["cases"] = [c for c in idx["cases"] if c["id"] not in dropped_ids]
    idx["count"] = len(idx["cases"])
    idx["generated_at"] = datetime.now(timezone.utc).isoformat()
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    print(f"\n完成: index 共 {idx['count']} 条案例"
          + (f", 媒体: {media_stats['with_media']} 条有图 / "
             f"{media_stats['no_media']} 条无图" if not args.no_media else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
