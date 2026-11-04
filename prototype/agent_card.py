# -*- coding: utf-8 -*-
"""出卡 Agent：把事件簇 + 核查包渲染成可发布的案例卡（markdown）。

卡上只放**能被溯源的东西**：
  - 簇内每条主张都带 case_id + 平台 + 原始 URL（可点开复核）；
  - 三层判定表显式区分"已填 / 待填"，没填就写"待外部检索"，不糊；
  - 证据缺口与建议检索词原样列出，读者能看到这张卡的边界在哪。

不做的事：不润色成结论、不补没有的来源。卡的可信度来自"缺口可见"，
而不是"看上去像结论"。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import evidence_rules as rules
from common import METADATA_DIR, ROOT

EVENTS = METADATA_DIR / "events.json"
PACKETS = METADATA_DIR / "verify_packets.json"
REPORTS = ROOT / "reports"

SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
LAYER_CN = {"event_layer": "事件层", "media_layer": "素材层", "claim_layer": "表述层"}
_clean_title = rules.clean_title   # 署名/投稿尾巴清理（与核查 Agent 共用一份规则）


def _case_urls(case_ids: list[str]) -> dict[str, dict]:
    p = ROOT / "dataset" / "index.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data.get("cases", []) if c["id"] in set(case_ids)}


def _clip(s: str, n: int) -> str:
    """按词边界截断（英文按空格，中文按字），超长补省略号——避免卡片标题断在半截词上。"""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-–—") + "…"


def render(ev: dict, packet: dict | None) -> str:
    cases = _case_urls(ev["case_ids"])
    L = []
    a = L.append
    head = _clip(_clean_title(ev["claims"][0]["title"] if ev["claims"] else ""), 46)
    a(f"# 事件卡 {ev['event_id']}｜{head}")
    a("")
    a(f"> 由真途（Zhentu）多智能体流水线自动生成 · "
      f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"簇内 {ev['size']} 条案例 / {len(ev['platforms'])} 个平台")
    a("")
    a("## 1. 事实基座（簇内成员，全部可点开复核）")
    a("")
    a("| # | 平台 | 主张（标题） | 案例 ID | 来源 |")
    a("|---|------|--------------|---------|------|")
    for i, c in enumerate(ev["claims"], 1):
        url = (cases.get(c["case_id"]) or {}).get("url") or ""
        link = f"[打开]({url})" if url else "—"
        title = _clean_title(c["title"] or "").replace("|", "／")
        a(f"| {i} | {c['platform']} | {title} | `{c['case_id']}` | {link} |")
    a("")
    a(f"- 平台分布：`{json.dumps(ev['platforms'], ensure_ascii=False)}`")
    a(f"- 表述变体：{ev['claim_variants']} 种（同一事件的不同说法，需逐变体判表述层）")
    a(f"- 簇内高频词（聚类依据，非结论）：{'、'.join(ev['top_tokens'][:8])}")
    a("")
    a("## 2. 三层判定（事件 / 素材 / 表述）")
    a("")
    a("> 三层必须分开判。混层是这个领域最常见的误判来源：事件真 ≠ 画面真 ≠ 表述不夸大。")
    a("> 下表的判定**全部归属核查源原文**：「来源称」= 引用核查站的说法，不是本卡自己的结论；"
      "「证据不足」= 抽不到可引用原文，需外部检索。")
    a("")
    a("| 层 | 要回答的问题 | 来源判定 | 依据（原文摘录 · 案例） |")
    a("|----|--------------|----------|--------------------------|")
    layers = (packet or {}).get("layers") or {}
    for key, cn in LAYER_CN.items():
        ld = layers.get(key) or {}
        label = ld.get("label_cn") or "证据不足"
        q = ld.get("question") or ""
        evs = ld.get("evidence") or []
        basis = "—"
        if evs:
            e = evs[0]
            cid = e.get("case_id", "")
            if cid:
                basis = f"{_clip(e.get('quote', ''), 110)} `{cid}`"
            else:
                # 外部检索证据：无 case_id，用来源域名标识（url 在引文段给出）
                basis = f"{_clip(e.get('quote', ''), 110)} · `{e.get('source', 'external')}`"
        a(f"| {cn} | {q} | **{label}** | {basis} |")
    a("")

    v = (packet or {}).get("verdict") or {}
    l1 = v.get("is_fake_source_suggested")
    if l1 is not None:
        a(f"- L1 二分类（来源建议）：**{l1}**（1 假 / 0 真 / -1 未定）· {v.get('is_fake_source_why', '')}")
    else:
        a("- L1 二分类：**未定**（无核查源评级可映射，需外部检索或人工标注）")
    labs = v.get("labels_suggested") or []
    if labs:
        a("- L2 多标签（来源建议）：" + "；".join(f"`{x['label']}`（{x['why'][:60]}）" for x in labs))
    a("")

    # 三层各自的完整引文：卡片最硬的部分就是这几句，全放出来
    quotes = []
    for key, cn in LAYER_CN.items():
        ld = layers.get(key) or {}
        for e in ld.get("evidence") or []:
            quotes.append((cn, e))
    if quotes:
        a("### 来源原文摘录（可逐句复核）")
        a("")
        for cn, e in quotes:
            src = e.get("source", "")
            fld = e.get("field", "")
            cid = e.get("case_id", "")
            if cid:
                url = (cases.get(cid) or {}).get("url") or ""
                tag = f"`{cid}`"
            else:
                # 外部检索证据：标识=来源域名，原文链接用发现自带的 url
                url = e.get("url") or ""
                tag = f"`{src}`"
            link = f" · [原文]({url})" if url else ""
            a(f"- **{cn}**（{src} / {fld} · {tag}{link}）：")
            a(f"  > {e.get('quote', '')}")
        a("")
    a("## 3. 证据缺口（这张卡的边界所在）")
    a("")
    closes = (packet or {}).get("gaps") or []
    if closes:
        for g in closes:
            a(f"- {SEV_ICON.get(g['severity'], '·')} **{g['code']}**（{g['severity']}）：{g['hint']}")
    else:
        a("- 预检未发现结构性缺口（不等于已验证，仍需外部检索）")
    a("")
    a("## 4. 下一步核查建议")
    a("")
    sugg = (packet or {}).get("search_suggestions") or []
    if sugg:
        a("建议检索式（按主张原始语言圈检索——中文主张去中文核查站，英文主张去英文核查站）：")
        a("")
        for s in sugg:
            a(f"- `{s}`")
    else:
        a("- （未生成检索建议：先跑 verify Agent）")
    a("")
    pc = (packet or {}).get("precheck") or {}
    if pc:
        a("预检事实（确定性统计，可直接引用）：")
        a("")
        a("| 指标 | 值 |")
        a("|------|-----|")
        for k, lbl in (("platform_count", "平台数"), ("variant_count", "表述变体数"),
                       ("authority_source_count", "权威来源域名数"),
                       ("domain_count", "独立域名数")):
            a(f"| {lbl} | {pc.get(k, '—')} |")
        a("")
    a("---")
    a("")
    a("*本卡由流水线生成。判定口径为「来源归属」：每条判定都能回到上方引用的原文；"
      "「证据不足」代表抽不到可引用原文，**不代表主张为真**，需按 "
      "`documentation/three_layer_judgment.md` 做外部检索后回填。*")
    return "\n".join(L) + "\n"


def run(params: dict) -> dict:
    ev_path = Path(params.get("events") or EVENTS)
    if not ev_path.is_absolute():
        ev_path = ROOT / ev_path
    if not ev_path.exists():
        return {"status": "error", "error": f"找不到 {ev_path}（先跑 cluster）"}
    ev_data = json.loads(ev_path.read_text(encoding="utf-8"))

    pk_path = Path(params.get("packets") or PACKETS)
    if not pk_path.is_absolute():
        pk_path = ROOT / pk_path
    packets = {}
    if pk_path.exists():
        pk_data = json.loads(pk_path.read_text(encoding="utf-8"))
        packets = {p["event_id"]: p for p in pk_data.get("packets", [])}

    want = str(params.get("event_id") or "").strip()
    all_events = ev_data.get("events", [])
    if want.lower() in ("all", "*", ""):
        targets = all_events[: int(params.get("limit") or 20)]
    else:
        targets = [e for e in all_events if e["event_id"] == want]
        if not targets:
            return {"status": "error",
                    "error": f"事件 {want} 不存在（现有 {len(all_events)} 个）"}

    REPORTS.mkdir(parents=True, exist_ok=True)
    written = []
    for ev in targets:
        md = render(ev, packets.get(ev["event_id"]))
        out = REPORTS / f"card_{ev['event_id']}.md"
        out.write_text(md, encoding="utf-8")
        written.append(str(out.relative_to(ROOT)).replace("\\", "/"))

    if len(written) > 1:
        idx = ["# 事件卡索引", "",
               f"共 {len(written)} 张，生成于 {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
               "", "| 事件 | 规模 | 平台 | 卡片 |", "|------|------|------|------|"]
        for ev in targets:
            idx.append(f"| {ev['event_id']} | {ev['size']} | "
                       f"{'/'.join(ev['platforms'])} | [card_{ev['event_id']}.md](card_{ev['event_id']}.md) |")
        (REPORTS / "index.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
        written.append("reports/index.md")

    return {
        "status": "ok",
        "outputs": {"cards": len(targets), "package_missing": not bool(packets),
                    "files": written[:5]},
        "artifacts": written,
        "metrics": {"cards": len(targets)},
        "next": [],
    }
