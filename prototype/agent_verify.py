# -*- coding: utf-8 -*-
"""核查 Agent：三层判定（事件/素材/表述）+ 证据缺口预检。

与 v1 的区别（v2）：v1 只摆空表，v2 **从已落盘的核查源原文里抽证据并落三层**。
判定口径统一是"来源归属"——脚本报的是「Snopes 给这条主张打了什么评级、原文哪句说它错」，
不是脚本自己下结论（规则与词表见 evidence_rules.py）。抽不到就写 insufficient。

为什么这么设计：三层判定的价值在于**每层都能指向一句原文**。
一旦允许脚本"综合判断"，三层立刻糊成一层，方法就自毁了。
所以 v2 增加的是**证据抽取能力**，不是判断能力。

输出 metadata/verify_packets.json（verify.v2）：
  layers.{event,media,claim} = {verdict, rationale, evidence[{case_id,source,field,quote}]}
  verdict.is_fake_source_suggested = 由核查源评级映射的 L1 建议值（标注"来源建议"）
  gaps = 仍缺什么（无核查源/单平台/无标签/标签冲突/无媒体…）
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import evidence_rules as rules
from common import METADATA_DIR, ROOT

EVENTS = METADATA_DIR / "events.json"
OUT = METADATA_DIR / "verify_packets.json"
DATASET = ROOT / "dataset"

AUTHORITY_DOMAINS = {
    "factcheck.afp.com", "afp.com", "snopes.com", "politifact.com", "apnews.com",
    "reuters.com", "bbc.com", "bbc.co.uk", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "npr.org", "vrt.be", "nos.nl", "rtve.es", "piyao.org.cn",
    "factcheck.org", "fullfact.org", "thepaper.cn", "sohu.com", "eastday.com",
    "maritime-executive.com", "timesofindia.indiatimes.com",
}

THREE_LAYERS = {
    "event_layer": "这件事本身发生了吗？有无独立信源（对方阵营／第三方核查／官方通报）？",
    "media_layer": "配图／视频／音频是否属于该事件？旧素材、AI 生成、跨事件错配都在这一层判。",
    "claim_layer": "说法是否超出证据？'彻底摧毁／全部投降／无一幸存'这类强断言有无支撑？",
}

VERDICT_CN = {
    "source_says_true": "来源称：成立",
    "source_says_false": "来源称：不实",
    "source_says_media_mismatch": "来源称：画面不符",
    "source_mentions": "来源提及（无评级）",
    "insufficient": "证据不足",
}

# 核查源评级 → L1 建议值（1 假 / 0 真 / -1 未定）。只做映射，不做推断。
RATING_TO_L1 = {"true": 0, "mixed": -1, "false": 1, "media": 1, "unproven": -1}

# 素材层措辞 → labeling_rules.md v0.2 的 L2 标签
MEDIA_TO_L2 = {"ai_or_synthetic": "cgi", "old_material": "contradictory_content",
               "mismatch": "contradictory_content"}


def _domain(url: str) -> str:
    try:
        h = urlparse(url or "").netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:  # noqa: BLE001
        return ""


def _is_authority(dom: str) -> bool:
    return any(dom == a or dom.endswith("." + a) for a in AUTHORITY_DOMAINS)


def _load_cases() -> dict[str, dict]:
    p = DATASET / "index.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data.get("cases", [])}


def _case_body(case: dict) -> str:
    paths = [p for p in case.get("files", {}).get("text", []) if p.endswith("text.txt")]
    if not paths:
        return ""
    p = DATASET / paths[0]
    if not p.exists():
        return ""
    body = p.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"^#.*$", "", body, count=1, flags=re.M)


def _query_base(ev: dict) -> str:
    """检索式基座：取一条主张标题，剥掉话题标签和标点，限 10 个词。

    为什么不用簇内高频词：高频词会被域名/频道名污染（foxnews / fnc / cnn），
    生成出 "fox brand fnc foxnews fact check" 这种没人会去搜的串。
    """
    for cl in ev.get("claims") or []:
        t = re.sub(r"#\S+", " ", cl.get("title") or "")
        t = re.sub(r"[|\-–—:;,\.\?!/\\()\[\]\"'“”‘’]+", " ", t)
        words = [w for w in t.split() if len(w) > 1]
        if len(words) >= 3:
            return " ".join(words[:10])
    return ev.get("event_id", "")


def _search_suggestions(ev: dict) -> list[str]:
    base = _query_base(ev)
    nums = [t.lstrip("#") for t in ev.get("top_tokens", []) if t.startswith("#")]
    sugg = [f"{base} fact check", f"{base} 辟谣", f"{base} debunk"]
    if nums:
        sugg.append(f"{base} {' '.join(nums[:2])}")
    return [s.strip() for s in sugg if s.strip()]


def _packet(ev: dict, cases: dict[str, dict]) -> dict:
    members_meta = [cases[cid] for cid in ev["case_ids"] if cid in cases]
    doms = [_domain(m.get("url", "")) for m in members_meta]
    auth = sorted({d for d in doms if _is_authority(d)})
    platforms = ev["platforms"]
    labels = {k: v for k, v in ev["labels"].items() if k not in ("None", "null")}
    media_cases = [m["id"] for m in members_meta
                   if any(m.get("files", {}).get(k) for k in ("image", "video", "wav"))]

    # 证据抽取：核查源排前面（judge_layers 按顺序取第一个有结论的）
    members = []
    for m in sorted(members_meta, key=lambda x: 0 if x.get("platform") in ("snopes", "piyao") else 1):
        body = _case_body(m)
        members.append({"case_id": m["id"], "platform": m.get("platform"),
                        "title": m.get("title") or "", "body": body,
                        "parsed": rules.parse_snopes(body) if m.get("platform") == "snopes" else {}})
    layers = rules.judge_layers(members)

    # L1 建议值 + L2 建议标签（都标注为"来源建议"，可被人工覆盖）
    l1, l1_why = None, ""
    l2: list[dict] = []
    for m in members:
        if m["platform"] != "snopes":
            continue
        rating = (m.get("parsed") or {}).get("rating", "")
        fam = rules.RATING_FAMILY.get(rating.lower(), "")
        if fam in RATING_TO_L1:
            l1 = RATING_TO_L1[fam]
            l1_why = f"snopes Rating={rating}（{m['case_id']}）"
            break
    mk = layers["media_layer"]
    if mk["verdict"] == "source_says_media_mismatch":
        for e in mk.get("evidence", []):
            for kind, lab in MEDIA_TO_L2.items():
                if kind == mk.get("kind"):
                    l2.append({"label": lab, "why": e.get("quote", "")[:120]})
    strong = layers["claim_layer"].get("strong_terms") or []
    if strong:
        l2.append({"label": "unsupported_content",
                   "why": "强断言词：" + "、".join(strong)})

    gaps = []
    if not auth:
        gaps.append({"code": "no_authority_source", "severity": "high",
                     "hint": "簇内没有任何核查机构/通讯社/官方来源，事件层无法内证，必须外查"})
    if len(platforms) < 2:
        gaps.append({"code": "single_platform", "severity": "medium",
                     "hint": "只在一个平台出现，跨阵营互证不成立"})
    if not labels:
        gaps.append({"code": "no_label", "severity": "high",
                     "hint": "簇内没有任何标签，需先完成 L1 二分类"})
    elif len({v > 0 for v in labels.values()}) > 1:
        gaps.append({"code": "label_conflict", "severity": "high",
                     "hint": f"簇内标签冲突（{labels}），要么归簇错，要么标注错，必须先裁"})
    if ev.get("claim_variants", 0) >= 3:
        gaps.append({"code": "many_variants", "severity": "medium",
                     "hint": f"{ev['claim_variants']} 种表述变体，需逐变体判 claim_layer"})
    if not media_cases:
        gaps.append({"code": "no_media", "severity": "low",
                     "hint": "簇内案例没有落地媒体文件，素材层无法判"})
    if len(set(doms)) == 1 and len(members_meta) > 1:
        gaps.append({"code": "single_domain", "severity": "medium",
                     "hint": f"簇内全部来自同一域名 {doms[0]}，是同一源头被搬运转述"})
    if all(v["verdict"] == "insufficient" for v in layers.values()):
        gaps.append({"code": "no_source_evidence", "severity": "high",
                     "hint": "三层都抽不到来源证据：需外部检索（见 search_suggestions）"})

    return {
        "event_id": ev["event_id"],
        "size": ev["size"],
        "platforms": platforms,
        "claims": ev["claims"],
        "case_ids": ev["case_ids"],
        "authority_domains": auth,
        "media_cases": media_cases,
        "precheck": {
            "platform_count": len(platforms),
            "variant_count": ev.get("claim_variants", 0),
            "authority_source_count": len(auth),
            "label_present": bool(labels),
            "domain_count": len(set(d for d in doms if d)),
        },
        "layers": {k: {**layers[k], "question": THREE_LAYERS[k],
                       "label_cn": VERDICT_CN.get(layers[k]["verdict"], layers[k]["verdict"])}
                   for k in THREE_LAYERS},
        "verdict": {
            "is_fake_source_suggested": l1,
            "is_fake_source_why": l1_why,
            "labels_suggested": l2,
            "note": "判定均归属来源原文；insufficient = 抽不到证据，需外部检索/人工",
        },
        "gaps": gaps,
        "search_suggestions": _search_suggestions(ev),
        "status": "source_grounded" if any(v["verdict"] != "insufficient"
                                          for v in layers.values()) else "initial",
    }


def run(params: dict) -> dict:
    src = Path(params.get("events") or EVENTS)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        return {"status": "error", "error": f"找不到 {src}（先跑 cluster）"}

    data = json.loads(src.read_text(encoding="utf-8"))
    cases = _load_cases()
    want = {s.strip() for s in str(params.get("event_ids") or "").split(",") if s.strip()}
    events = [e for e in data.get("events", []) if not want or e["event_id"] in want]
    if not events:
        return {"status": "error", "error": f"事件筛选为空（event_ids={sorted(want)}）"}

    packets = [_packet(e, cases) for e in events]

    # 统计：每层拿到多少条来源证据（这是本 Agent 的"产出率"，也是下一步的靶子）
    def cnt(layer: str) -> dict:
        c: dict[str, int] = {}
        for p in packets:
            v = p["layers"][layer]["verdict"]
            c[v] = c.get(v, 0) + 1
        return c

    payload = {
        "schema_version": "verify.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_events": str(src.relative_to(ROOT)).replace("\\", "/"),
        "count": len(packets),
        "by_severity": {
            s: sum(1 for p in packets for g in p["gaps"] if g["severity"] == s)
            for s in ("high", "medium", "low")
        },
        "clean": sum(1 for p in packets if not p["gaps"]),
        "source_grounded": sum(1 for p in packets if p["status"] == "source_grounded"),
        "layer_coverage": {k: cnt(k) for k in THREE_LAYERS},
        "packets": packets,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "status": "ok",
        "outputs": {"path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
                    "count": len(packets),
                    "source_grounded": payload["source_grounded"],
                    "layer_coverage": payload["layer_coverage"],
                    "by_severity": payload["by_severity"],
                    "note": "三层判定均归属来源原文；抽不到即 insufficient"},
        "artifacts": [str(OUT.relative_to(ROOT)).replace("\\", "/")],
        "metrics": {"events": len(packets),
                    "source_grounded": payload["source_grounded"]},
        "next": ["card"],
    }
