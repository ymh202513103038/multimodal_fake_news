# -*- coding: utf-8 -*-
"""机助预标：为 98 条案例生成「来源建议」级 L1/L2 预标（2026-11-04，目标轮 r2）。

铁律（与三层判定同源）：
  AI 只搬"核查源已经说过的话"，不冒充判断。
  - Snopes 案例才有 L1 建议（评级映射 RATING_TO_L1，high 置信）；
  - piyao 是辟谣种子层：它的 debunk 对象主张没有被结构化提取（信号句多为泛化
    辟谣表述），AI 给 L1 就是冒充判断 → 一律 null + 人工待办；
  - YouTube/X 无核查源 → null；正文含强断言词的只在依据列提示，不定性。
  AI 列与人工列物理分开（AI_is_fake / is_fake），人工标完后仍走
  label_sheet.csv → make_cases 的正式回填链路，本脚本产物只作参考。

用法（openpyxl 装在全局 Python，不在 venv）：
  python prototype\\make_label_suggestions.py --dry
  python prototype\\make_label_suggestions.py

输出：
  metadata/label_suggestions.json           逐条建议（含引文依据）
  metadata/标注表_98条_机助预标.xlsx         人工工作簿：AI 列 + 人工列分列并存
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from common import DATASET_DIR, METADATA_DIR, ROOT

INDEX = DATASET_DIR / "index.json"
PACKETS = METADATA_DIR / "verify_packets.json"
OUT_JSON = METADATA_DIR / "label_suggestions.json"
OUT_XLSX = METADATA_DIR / "标注表_98条_机助预标.xlsx"
PILOT_XLSX = METADATA_DIR / "标注表_试点20条.xlsx"

L2_LABELS = {"cgi", "false_title", "false_speech", "temporal_edit",
             "contradictory_content", "unsupported_content"}


def _snippet(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _case_text(c: dict) -> str:
    for p in (c.get("files") or {}).get("text", []):
        if p.endswith("text.txt"):
            f = DATASET_DIR / p
            if f.exists():
                return f.read_text(encoding="utf-8", errors="replace")
    return ""


def load_pilot_ids() -> set:
    """试点 20 条名单直接读试点工作簿，避免名单漂移。"""
    try:
        from openpyxl import load_workbook
        ws = load_workbook(PILOT_XLSX, read_only=True)["标注表"]
        return {str(r[0].value) for r in ws.iter_rows(min_row=2) if r[0].value}
    except Exception:  # noqa: BLE001
        return set()


def build_suggestion(case: dict, packet: dict) -> dict:
    """按平台的诚实口径生成 AI 建议。"""
    plat = case.get("platform")
    rec = {
        "id": case["id"], "platform": plat, "lang": case.get("lang"),
        "event": case.get("event"), "url": case.get("url"),
        "title": _snippet(case.get("title") or "", 120),
        "ai_l1": None, "ai_confidence": "none",
        "ai_why": "", "ai_l2": [], "ai_todo": "",
    }
    v = (packet or {}).get("verdict") or {}
    layers = (packet or {}).get("layers") or {}
    if plat == "snopes":
        rec["ai_l1"] = v.get("is_fake_source_suggested")
        rec["ai_confidence"] = "high"
        ev = (layers.get("event_layer", {}).get("evidence") or [{}])[0]
        rec["ai_why"] = _snippet(v.get("is_fake_source_why") or "", 120)
        q = _snippet(ev.get("quote") or "", 200)
        if q:
            rec["ai_why"] += f"；原文：{q}"
        rec["ai_l2"] = [x.get("label") for x in (v.get("labels_suggested") or [])
                        if x.get("label") in L2_LABELS]
        rec["ai_todo"] = "人工复核评级映射与 L2 是否成立；L3 grounding 对照核查原文补"
    elif plat == "piyao":
        rec["ai_todo"] = ("辟谣种子层：debunk 对象主张未结构化提取，"
                          "人工读原文确定所辟主张后，主张定 L1、本文按 evidence_url 记证据")
        rec["ai_why"] = "piyao 信号句为泛化辟谣表述，不足以给任何具体主张定真假"
    else:
        cl = layers.get("claim_layer") or {}
        strong = cl.get("strong_terms") or []
        if strong:
            rec["ai_why"] = f"正文含强断言词（{'、'.join(strong[:6])}），无核查源证据"
        else:
            rec["ai_why"] = "无核查源证据"
        rec["ai_l2"] = [x.get("label") for x in (v.get("labels_suggested") or [])
                        if x.get("label") == "unsupported_content"]
        rec["ai_todo"] = "需人工判定或先走外部检索回填（见对应事件卡检索式）"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印统计，不写文件")
    a = ap.parse_args()

    cases = json.loads(INDEX.read_text(encoding="utf-8")).get("cases", [])
    pk = json.loads(PACKETS.read_text(encoding="utf-8")).get("packets", [])
    by_case: dict[str, dict] = {}
    for p in pk:
        for cid in p.get("case_ids", []):
            by_case[cid] = p
    pilot = load_pilot_ids()

    recs = [build_suggestion(c, by_case.get(c["id"])) for c in cases]
    for r, c in zip(recs, cases):
        r["pilot"] = c["id"] in pilot
        r["has_media"] = any((c.get("files") or {}).get(k) for k in ("image", "video", "wav"))
        r["summary"] = _snippet(_case_text(c), 150)
        r["evidence_url"] = c.get("url") or ""

    stat_l1 = Counter(str(r["ai_l1"]) for r in recs)
    stat_plat = Counter(r["platform"] for r in recs)
    print(f"cases={len(recs)}  试点命中={sum(r['pilot'] for r in recs)}/20")
    print("AI_L1 分布:", dict(stat_l1))
    print("平台分布:", dict(stat_plat))
    if a.dry:
        return 0

    OUT_JSON.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "note": "AI=来源建议级：仅 Snopes 评级映射；piyao/社媒一律待人工。"
                 "人工列回填仍走 label_sheet.csv → make_cases。",
         "count": len(recs), "items": recs}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("JSON ->", OUT_JSON.relative_to(ROOT).as_posix())

    _write_xlsx(recs, pilot)
    print("XLSX ->", OUT_XLSX.relative_to(ROOT).as_posix())
    return 0


def _write_xlsx(recs: list, pilot: set) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "标注表"
    headers = ["sample_id", "平台", "lang", "event", "试点", "标题", "正文摘要",
               "有媒体", "原帖链接",
               "AI_is_fake", "AI_置信", "AI_依据", "AI_fake_types",
               "is_fake(人工)", "fake_types(人工)", "grounding_title",
               "grounding_speech", "grounding_visual", "evidence_url(人工)",
               "rationale(人工)", "annotator", "labeled_at"]
    ws.append(headers)
    ai_fill = PatternFill("solid", fgColor="FFF3E0")
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    for col, w in zip(range(1, len(headers) + 1),
                      (34, 9, 7, 7, 6, 46, 40, 7, 40, 10, 8, 46, 16,
                       12, 16, 18, 14, 16, 34, 34, 10, 11)):
        ws.column_dimensions[get_column_letter(col)].width = w
    for r in recs:
        ws.append([r["id"], r["platform"], r["lang"], r["event"],
                   "是" if r["pilot"] else "", r["title"], r["summary"],
                   "是" if r["has_media"] else "否", r["url"],
                   r["ai_l1"], r["ai_confidence"], r["ai_why"],
                   ";".join(r["ai_l2"]),
                   None, None, None, None, None, None, None, None, None])
    for row in ws.iter_rows(min_row=2, min_col=10, max_col=13):
        for cell in row:
            cell.fill = ai_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    g = wb.create_sheet("预标口径")
    g.column_dimensions["A"].width = 110
    lines = [
        "【AI 列=来源建议级，不是标注】三条铁律：",
        "1. 只有 Snopes 案例有 AI_is_fake（评级映射：true→0 / mixture·unproven→-1 / false·media→1），置信=high；",
        "2. piyao 是辟谣种子层，debunk 对象主张未结构化 → AI 一律不给 L1，人工先读原文定所辟主张；",
        "3. YouTube/X 无核查源 → AI 一律不给 L1；含强断言词只在 AI_依据 提示，不定性。",
        "",
        "【人工怎么用】",
        "- 橙色列（J:M）只是参考，复核后把结论填进白色人工列（N:V）；",
        "- 人工列回填仍走正式链路：label_sheet.csv → make_cases（AI 列不进 index）；",
        "- 试点=是 的 20 行来自 标注表_试点20条.xlsx（kappa 试点）；",
        "- AI_依据 里的原文引文都可点开 evidence_url 逐句复核。",
    ]
    for ln in lines:
        g.append([ln])
    g["A1"].font = bold

    wb.save(OUT_XLSX)


if __name__ == "__main__":
    raise SystemExit(main())
