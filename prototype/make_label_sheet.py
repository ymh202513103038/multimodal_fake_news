"""第 5 步预备：从 meta jsonl 抽样生成标注表（CSV），衔接 docs/labeling_rules.md。

用法（默认从 X 的 meta 抽，可复现，重复运行自动去重追加）：
  python make_label_sheet.py --samples 30
  python make_label_sheet.py --meta ../metadata/youtube_meta.jsonl --source youtube --samples 10

输出：metadata/label_sheet.csv
  列 = labeling_rules.md 的 schema：sample_id, source, text, media_local,
       is_fake, modality_type, evidence_url, rationale, annotator, labeled_at
  其中 is_fake / modality_type / evidence_url / rationale / annotator / labeled_at
  留空给标注人填写；标注前先读 docs/labeling_rules.md（kappa ≥ 0.6 才放量）。
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from common import METADATA_DIR, setup_logger

log = setup_logger("label_sheet")
OUT = METADATA_DIR / "label_sheet.csv"

COLUMNS = ["sample_id", "source", "text", "media_local",
           "is_fake", "modality_type", "evidence_url", "rationale",
           "annotator", "labeled_at"]


def load_existing_ids() -> set:
    if not OUT.exists():
        return set()
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        return {row.get("sample_id") for row in csv.DictReader(f) if row.get("sample_id")}


def main() -> None:
    ap = argparse.ArgumentParser(description="抽样生成标注表 CSV")
    ap.add_argument("--meta", default=str(METADATA_DIR / "twitter_meta.jsonl"))
    ap.add_argument("--source", default="x", help="来源平台标识，写入 source 列")
    ap.add_argument("--samples", type=int, default=30, help="本次新增条数（试点建议 20~30）")
    ap.add_argument("--seed", type=int, default=7, help="随机种子，保证可复现")
    a = ap.parse_args()

    meta_path = Path(a.meta)
    if not meta_path.exists():
        raise SystemExit(f"找不到 {meta_path}：先跑 step1+step2（或 step3）生成 meta。")

    import json
    pool = []
    for line in open(meta_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("mode") == "mock":
            continue  # mock 数据不进标注池
        rid = r.get("tweet_id") or r.get("video_id")
        if not rid:
            continue
        text = (r.get("text") or r.get("title") or "").strip()
        if not text:
            continue
        media = "|".join(r.get("media_local") or [])
        pool.append({
            "sample_id": f"{a.source}:{rid}",
            "source": a.source,
            "text": text.replace("\n", " "),
            "media_local": media,
        })

    existing = load_existing_ids()
    fresh = [p for p in pool if p["sample_id"] not in existing]
    if not fresh:
        log.info("池子里没有新样本可抽（已有 %d 条在表中）。", len(existing))
        return
    picked = random.Random(a.seed).sample(fresh, min(a.samples, len(fresh)))

    new_file = not OUT.exists()
    with open(OUT, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        for row in picked:
            w.writerow({**row, **{k: "" for k in
                          ("is_fake", "modality_type", "evidence_url",
                           "rationale", "annotator", "labeled_at")}})

    log.info("新增 %d 条标注任务（池 %d，跳过已存在 %d）-> %s",
             len(picked), len(pool), len(pool) - len(fresh), OUT)
    log.info("标注前先读 docs/labeling_rules.md：is_fake∈{1,0,-1}，"
             "双人独立标，kappa<0.6 时修订规范再标。")


if __name__ == "__main__":
    main()
