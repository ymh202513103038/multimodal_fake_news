# -*- coding: utf-8 -*-
"""核查 Agent：为每个事件簇生成**三层判定工作表** + 证据缺口预检。

这条 Agent 的边界是刻意划死的（关系到项目可信度）：
  - 它**不判真假**。输出里 event_layer / media_layer / claim_layer 一律为 null，
    由人（或接了外部检索的 LLM）填；agent 只负责把"要回答什么"和"缺什么"摆出来。
  - 理由：三层判定（事件层/素材层/表述层）是项目里被实战检验过的核心方法，
    它的价值恰恰在于"不混层"。让脚本自动产出 verdict 只会把三层糊回一层，
    等于自毁方法。所以这里只做**证据缺口预检**——这是脚本擅长且不会撒谎的部分。

三层的意思（写进输出，供标注者/LLM 直接照做）：
  event_layer  事件本身是否发生（有独立信源？交战双方/第三方？）
  media_layer  配图/视频/音频是不是这个事件的（旧素材？AI 生成？错配？）
  claim_layer  表述是否超出证据（"彻底摧毁""全部投降"这类强断言有无支撑？）

输出 metadata/verify_packets.json（verify.v1）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from common import METADATA_DIR, ROOT

EVENTS = METADATA_DIR / "events.json"
OUT = METADATA_DIR / "verify_packets.json"

# 能当"独立信源"的域名族（核查机构 / 通讯社 / 主流媒体）。
# 只是**识别**用，不代表出现在这些域名上就等于结论正确。
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
RE_TAG = re.compile(r"#\S+")


def _query_base(ev: dict) -> str:
    """检索式基座：取一条主张标题，剥掉话题标签和标点，限 10 个词。

    为什么不用簇内高频词：高频词会被域名/频道名污染（foxnews / fnc / cnn），
    生成出 "fox brand fnc foxnews fact check" 这种没人会去搜的串。
    人真正会去查的是主张本身的措辞。
    """
    for cl in ev.get("claims") or []:
        t = RE_TAG.sub(" ", cl.get("title") or "")
        t = re.sub(r"[|\-–—:;,\.\?!/\\()\[\]\"'“”‘’]+", " ", t)
        words = [w for w in t.split() if len(w) > 1]
        if len(words) >= 3:
            return " ".join(words[:10])
    return ev.get("event_id", "")


def _domain(url: str) -> str:
    try:
        h = urlparse(url or "").netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:  # noqa: BLE001
        return ""


def _is_authority(dom: str) -> bool:
    return any(dom == a or dom.endswith("." + a) for a in AUTHORITY_DOMAINS)


def _load_cases() -> dict[str, dict]:
    p = ROOT / "dataset" / "index.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data.get("cases", [])}


def _search_suggestions(ev: dict) -> list[str]:
    """从簇内主张里抽检索词，生成"该去哪儿查"的建议（不是结论）。"""
    base = _query_base(ev)
    nums = [t.lstrip("#") for t in ev.get("top_tokens", []) if t.startswith("#")]
    sugg = [f"{base} fact check", f"{base} 辟谣", f"{base} debunk"]
    if nums:
        sugg.append(f"{base} {' '.join(nums[:2])}")
    return [s.strip() for s in sugg if s.strip()]


def _packet(ev: dict, cases: dict[str, dict]) -> dict:
    members = [cases[cid] for cid in ev["case_ids"] if cid in cases]
    doms = [_domain(m.get("url", "")) for m in members]
    auth = sorted({d for d in doms if _is_authority(d)})
    platforms = ev["platforms"]
    labels = {k: v for k, v in ev["labels"].items() if k not in ("None", "null")}
    has_media = [m["id"] for m in members
                 if any(m.get("files", {}).get(k) for k in ("image", "video", "wav"))]

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
                     "hint": f"{ev['claim_variants']} 种表述变体，需逐变体判 claim_layer，不能只判一个"})
    if not has_media:
        gaps.append({"code": "no_media", "severity": "low",
                     "hint": "簇内案例没有落地媒体文件，素材层无法判（多模态样本要用带媒体的案例）"})
    if len(set(doms)) == 1 and len(members) > 1:
        gaps.append({"code": "single_domain", "severity": "medium",
                     "hint": f"簇内全部来自同一域名 {doms[0]}，是同一源头被搬运转述"})

    return {
        "event_id": ev["event_id"],
        "size": ev["size"],
        "platforms": platforms,
        "claims": ev["claims"],
        "case_ids": ev["case_ids"],
        "authority_domains": auth,
        "media_cases": has_media,
        "precheck": {
            "platform_count": len(platforms),
            "variant_count": ev.get("claim_variants", 0),
            "authority_source_count": len(auth),
            "label_present": bool(labels),
            "domain_count": len(set(d for d in doms if d)),
        },
        "layers": dict(THREE_LAYERS),
        "verdict": {"event_layer": None, "media_layer": None, "claim_layer": None,
                    "is_fake": None,                 # L1：1 假 / 0 真 / -1 未定
                    "labels": [],                    # L2 多标签（labeling_rules.md v0.2）
                    "note": "留空由外部检索/人工填；本 Agent 不产出判定"},
        "gaps": gaps,
        "search_suggestions": _search_suggestions(ev),
        "status": "initial",                          # initial → verified（外查后由人或 LLM 改）
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
    payload = {
        "schema_version": "verify.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_events": str(src.relative_to(ROOT)).replace("\\", "/"),
        "count": len(packets),
        "by_severity": {
            s: sum(1 for p in packets for g in p["gaps"] if g["severity"] == s)
            for s in ("high", "medium", "low")
        },
        "clean": sum(1 for p in packets if not p["gaps"]),
        "packets": packets,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "status": "ok",
        "outputs": {"path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
                    "count": len(packets), "clean": payload["clean"],
                    "by_severity": payload["by_severity"],
                    "note": "verdict 全为 null：本 Agent 只给缺口，不判真假"},
        "artifacts": [str(OUT.relative_to(ROOT)).replace("\\", "/")],
        "metrics": {"events": len(packets),
                    "high_gaps": payload["by_severity"]["high"]},
        "next": ["card"],
    }
