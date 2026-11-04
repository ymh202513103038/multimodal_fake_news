# -*- coding: utf-8 -*-
"""回填 dataset/index.json 的 event 与 lang 字段（2026-11-04，目标轮 r1）。

- event：从 metadata/events.json 的簇成员关系回填（归簇产物 = 事件归属的真源）；
- lang：lang_detect.detect_lang 的提示值（zh/kn/en/pt/id/und），只填空值不覆盖；
- 写前备份到 metadata/index_backup_<日期>.json，原子写（tmp + replace）；
- label / annotator / reason 等人工字段不碰。

用法：
  python prototype/backfill_index_fields.py --dry   # 只统计不写盘
  python prototype/backfill_index_fields.py         # 备份+回填+原子写
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone

from common import DATASET_DIR, METADATA_DIR, ROOT
from lang_detect import detect_lang

EVENTS = METADATA_DIR / "events.json"
INDEX = DATASET_DIR / "index.json"


def _case_text(c: dict) -> str:
    parts = [c.get("title") or ""]
    for p in (c.get("files") or {}).get("text", []):
        if p.endswith("text.txt"):
            f = DATASET_DIR / p
            if f.exists():
                parts.append(f.read_text(encoding="utf-8", errors="replace")[:2000])
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计，不写盘")
    ap.add_argument("--force-lang", action="store_true",
                    help="重算全部 lang（默认只填空值，不覆盖已有人工值）")
    a = ap.parse_args()

    cases = json.loads(INDEX.read_text(encoding="utf-8")).get("cases", [])
    data = json.loads(EVENTS.read_text(encoding="utf-8"))
    ev_map: dict[str, str] = {}
    for ev in data.get("events", []):
        for cid in ev.get("case_ids", []):
            ev_map[cid] = ev["event_id"]

    ev_filled = lang_filled = 0
    lang_stat: Counter = Counter()
    unmatched = 0
    for c in cases:
        if not c.get("event") and ev_map.get(c["id"]):
            c["event"] = ev_map[c["id"]]
            ev_filled += 1
        if not c.get("event"):
            unmatched += 1
        if a.force_lang or not c.get("lang"):
            c["lang"] = detect_lang(_case_text(c))
            lang_filled += 1
        lang_stat[c.get("lang") or "und"] += 1

    print(f"cases={len(cases)}  event回填={ev_filled}  event仍空={unmatched}  lang回填={lang_filled}")
    print("lang分布:", dict(lang_stat.most_common()))
    if a.dry:
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup = METADATA_DIR / f"index_backup_{stamp}.json"
    if not backup.exists():
        backup.write_text(INDEX.read_text(encoding="utf-8"), encoding="utf-8")
        print("备份 ->", backup.relative_to(ROOT).as_posix())
    tmp = INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "count": len(cases),
         "schema": "ex-style case index: id/event/lang/topic/label/reason + modality files",
         "cases": cases},
        ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, INDEX)
    print("写回 ->", INDEX.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
