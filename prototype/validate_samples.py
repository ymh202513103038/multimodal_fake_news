"""抽样校验：随机挑 N 条，自动检查媒体文件是否完整、文本是否为空。

用法：python validate_samples.py --sample 5
输出：控制台表格 + ../logs/validation_report.json
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from common import LOGS_DIR, METADATA_DIR, ROOT, read_jsonl, setup_logger

log = setup_logger("validate")
META = METADATA_DIR / "twitter_meta.jsonl"
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")


def check_media(rel: str) -> tuple[bool, str, int]:
    p = ROOT / rel
    if not p.exists():
        return False, "文件缺失", 0
    size = p.stat().st_size
    if size == 0:
        return False, "空文件", 0
    suffix = p.suffix.lower()
    head = p.read_bytes()[:64]
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        if head.startswith(IMAGE_MAGIC):
            return True, "图片魔数正常", size
        return False, "图片魔数异常", size
    if suffix in {".mp4", ".mov", ".webm", ".m4a", ".mp3"}:
        if b"ftyp" in head or head.startswith(b"ID3") or b"OggS" in head:
            return True, "音视频头正常", size
        return False, "音视频头异常", size
    return True, "未知类型（仅检查大小）", size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default=str(META))
    ap.add_argument("--sample", type=int, default=5)
    a = ap.parse_args()

    records = list(read_jsonl(Path(a.meta)))
    if not records:
        raise SystemExit(f"没有可校验的记录：{a.meta}（先跑 step1/step2）")
    n = min(a.sample, len(records))
    picked = random.Random(7).sample(records, n)

    report = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "total": len(records), "sampled": n, "samples": []}
    fails = 0
    for idx, rec in enumerate(picked, 1):
        text = (rec.get("text") or "").strip()
        text_ok = bool(text)
        media_rows = []
        for rel in rec.get("media_local") or []:
            ok, note, size = check_media(rel)
            media_rows.append({"path": rel, "ok": ok, "note": note, "bytes": size})
            fails += 0 if ok else 1
        fails += 0 if text_ok else 1
        print(f"[{idx}/{n}] tweet_id={rec.get('tweet_id')}  文本={'PASS' if text_ok else 'FAIL'}"
              f"  媒体={len(media_rows)}")
        for row in media_rows:
            print(f"    {'PASS' if row['ok'] else 'FAIL'}  {row['bytes']:>8} B  {row['note']}"
                  f"  {row['path']}")
        print(f"    文本: {text[:70]}")
        report["samples"].append({"tweet_id": rec.get("tweet_id"), "text_ok": text_ok,
                                  "media": media_rows})

    report_path = LOGS_DIR / "validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("校验完成：%d 个样本，%d 项失败 -> %s", n, fails, report_path)
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
