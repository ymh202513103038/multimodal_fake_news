# -*- coding: utf-8 -*-
"""三层判定规则集 v1：从**已落盘的核查源原文**里抽取证据，不凭空判断。

为什么是"来源归属"而不是"脚本判定"：
  问"这条主张假不假"要人/模型拍板；问"Snopes 给这条主张打了什么评级、原文哪句话
  说它错"是**抽取**，可逐字复核。三层判定（事件/素材/表述）的价值就在于每层
  都能指向一句原文，所以本模块只产出两种东西：
      verdict = source_says_true | source_says_mixed | source_says_false
                | source_mentions | insufficient
      evidence = [{case_id, source, quote}]        ← 必须能点回原文
  抽不到就返回 insufficient，绝不猜。

三份语料的形态（实测）：
  Snopes   高度结构化：`Claim:` / `Rating:` / `About this rating` /
           `What's True` / `What's False` / `What's Undetermined`。→ 直接映射三层：
           What's True→事件层（事情的真部分），What's False→表述层（夸大/错的部分），
           素材类措辞（AI生成/旧素材/错配）→素材层。
  piyao    中文辟谣站，行文自由；抽"谣言/不实/实为/真相"等信号句。
  YouTube/X 没有核查结论，只有主张本身 → 只能给主张层线索（强断言词表），判不了真假。

可扩展点：新增核查站时，只需在 `SOURCE_PARSERS` 里加一个解析函数，
或在 `media_markers` / `strong_claim_terms` 词表里补词，其余逻辑不变。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 评级映射

# Snopes 的 Rating → 三层含义。注意 mixed 类不能压成"半真半假"一个数：
# 它们恰恰是三层判定要吃下的情形（事件真 + 表述夸大 / 素材错配）。
RATING_FAMILY = {
    "true": "true", "mostly true": "mixed", "mixture": "mixed", "mostly false": "mixed",
    "false": "false", "fake": "false", "scam": "false", "fabricated": "false",
    "miscaptioned": "media", "misattributed": "media", "outdated": "mixed",
    "unproven": "unproven", "legend": "unproven", "satire": "false",
    "originated as satire": "false", "altered": "media",
    "correct attribution": "true", "research in progress": "unproven",
}

SN_SECTIONS = ("What's True", "What's False", "What's Undetermined", "About this rating")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


RE_BYLINE = re.compile(
    r"\s*(Written by:|Posted by|About this rating|The source for this claim).*$",
    flags=re.I | re.S)


def clean_title(t: str) -> str:
    """核查站标题常带署名/投稿尾巴（"Written by: … Oct. 16, 2026 …"），显示前切掉。"""
    return RE_BYLINE.sub("", (t or "").strip()).strip()


def split_sentences(text: str) -> list[str]:
    """中英混排分句：先按句末标点切，再滤掉过短片段。"""
    parts = re.split(r"(?<=[。！？；!?;])\s*|(?<=\.)\s+(?=[A-Z\"'“])", text or "")
    return [_clean(p) for p in parts if len(_clean(p)) >= 12]


# ---------------------------------------------------------------- Snopes

def parse_snopes(body: str) -> dict:
    """抽 Claim / Rating / What's True / What's False / What's Undetermined。"""
    lines = [ln.strip() for ln in (body or "").splitlines()]
    out = {"title": "", "claim": "", "rating": "", "sections": {}}
    for ln in lines:
        if ln.startswith("# "):
            out["title"] = _clean(ln[2:])
            break

    # Claim: 与其后续行（到 Rating: 为止）
    buf: list[str] = []
    mode = None
    for i, ln in enumerate(lines):
        if ln == "Claim:":
            mode = "claim"
            continue
        if ln == "Rating:":
            mode = "rating"
            if i + 1 < len(lines):
                out["rating"] = _clean(lines[i + 1])
            continue
        if ln in SN_SECTIONS:
            mode = f"sec:{ln}"
            out["sections"][ln] = []
            continue
        if ln in ("About this rating",) or ln.startswith("Sources") or ln == "Sources":
            mode = None
            continue
        if mode == "claim" and ln:
            buf.append(ln)
        elif mode == "rating":
            mode = None
        elif isinstance(mode, str) and mode.startswith("sec:") and ln:
            out["sections"][mode[4:]].append(ln)
    out["claim"] = _clean(" ".join(buf))
    out["sections"] = {k: _clean(" ".join(v)) for k, v in out["sections"].items()}
    return out


# ---------------------------------------------------------------- 三层的信号词

# 素材层：只要核查原文出现这些措辞，说明"画面/音频与事件不符"正是被核过的点。
MEDIA_MARKERS = [
    (r"digitally altered|doctored|manipulated|synthetic|deepfake|AI[- ]generated|"
     r"generated (?:by|with) AI|computer-generated", "ai_or_synthetic"),
    (r"old footage|older footage|previous(?:ly)? (?:video|footage|clip)|"
     r"recycled|from an earlier|archival (?:footage|video)", "old_material"),
    (r"miscaptioned|mislabeled|misattributed|captioned|wrong(?:ly)? (?:attributed|labeled)|"
     r"not from this event|unrelated (?:video|footage|image)", "mismatch"),
    (r"AI合成|人工智能生成|合成视频|深度伪造|换脸|生成式AI", "ai_or_synthetic"),
    (r"旧视频|旧画面|旧素材|挪用于|移花接木|张冠李戴|并非同一事件|实为.{0,12}画面", "old_material"),
    (r"画面与.{0,12}不符|视频非|图片非|错配|断章取义|剪辑拼接", "mismatch"),
]

# 表述层：强断言词——出现即提示"表述可能超出证据"，但**只提示不判**，
# 必须同时有核查源原文才算证据。
STRONG_CLAIM_TERMS = [
    "彻底", "全部", "无一", "无一幸存", "毁灭", "摧毁", "全歼", "投降", "已死", "死亡",
    "百分百", "100%", "完全", "永久", "史上最", "首次证实", "官方承认", "承认误炸",
    "completely destroyed", "wiped out", "all killed", "100%", "totally",
    "confirmed", "admitted", "entirely", "never", "always",
]

# 中文辟谣站信号句。注意 piyao 语料里混着大量"专项行动通报/倡议书/沙龙纪要"，
# 里面出现"辟谣"只是站名或口号（如"e起辟谣 清朗有我"），不是核查结论。
# 所以先排除这些版式句，再要求同句里出现"主张式"内容，避免假阳性。
PIYAO_SIGNALS = [
    (r"谣言|系谣言|纯属谣言|不实信息|虚假信息|不实", "rumor"),
    (r"实为|真相是|事实是|经核实|经查证|查明", "fact"),
]
PIYAO_NOISE = re.compile(
    r"倡议书|沙龙|主题活动|专项行动|通报如下|典型案例|公众号|责任编辑|来源：|时间：|"
    r"e起辟谣|清朗有我|点击视频|值得一起读|名单|附件")
PIYAO_CLAIMISH = re.compile(r"称|称有|据传|网传|消息|视频显示|图片显示|发布|表示|声称|所谓")


def media_markers(text: str) -> list[dict]:
    """在核查原文里找"素材层"措辞，返回原句（截断 160 字）。"""
    hits = []
    seen = set()
    for sent in split_sentences(text):
        for pat, kind in MEDIA_MARKERS:
            if re.search(pat, sent, flags=re.I):
                key = (kind, sent[:40])
                if key not in seen:
                    seen.add(key)
                    hits.append({"kind": kind, "quote": sent[:220]})
                break
    return hits


def strong_claims(title: str) -> list[str]:
    low = (title or "").lower()
    return [w for w in STRONG_CLAIM_TERMS if w.lower() in low]


def piyao_signals(body: str, limit: int = 3) -> list[dict]:
    """只在"主张式"句子里找信号；版式句（通报/倡议/来源）一律跳过。"""
    hits = []
    for sent in split_sentences(body):
        if PIYAO_NOISE.search(sent):
            continue
        for pat, kind in PIYAO_SIGNALS:
            if re.search(pat, sent) and PIYAO_CLAIMISH.search(sent):
                hits.append({"kind": kind, "quote": sent[:220]})
                break
        if len(hits) >= limit:
            break
    return hits


# ---------------------------------------------------------------- 汇总成三层

def judge_layers(members: list[dict]) -> dict:
    """members: [{case_id, platform, title, body}]；返回三层判定 + 证据。

    members 的顺序即优先级：核查源（snopes/piyao）优先于社媒案例。
    """
    fact = [m for m in members if m.get("platform") in ("snopes", "piyao")]
    social = [m for m in members if m.get("platform") not in ("snopes", "piyao")]

    layers = {
        "event_layer": {"verdict": "insufficient", "evidence": [], "rationale": ""},
        "media_layer": {"verdict": "insufficient", "evidence": [], "rationale": ""},
        "claim_layer": {"verdict": "insufficient", "evidence": [], "rationale": ""},
    }

    # ---- 事件层 / 表述层：优先用 Snopes 的结构化段落
    for m in fact:
        if m.get("platform") != "snopes":
            continue
        sn = m["parsed"]
        rating = sn.get("rating", "")
        fam = RATING_FAMILY.get(rating.lower(), "")
        truth = sn["sections"].get("What's True", "")
        false = sn["sections"].get("What's False", "")
        undet = sn["sections"].get("What's Undetermined", "")

        if truth:
            layers["event_layer"] = {
                "verdict": "source_says_true",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "What's True", "quote": truth[:300]}],
                "rationale": f"Snopes 评级「{rating}」；其 What's True 段落给出了事件成立的部分。"}
        elif fam == "true":
            layers["event_layer"] = {
                "verdict": "source_says_true",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "Rating", "quote": f"Rating: {rating}"}],
                "rationale": f"Snopes 评级「{rating}」，主张整体成立。"}
        elif fam == "false" and not truth:
            layers["event_layer"] = {
                "verdict": "source_says_false",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "Rating", "quote": f"Rating: {rating}"}],
                "rationale": f"Snopes 评级「{rating}」，主张与事实不符。"}

        if false:
            layers["claim_layer"] = {
                "verdict": "source_says_false",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "What's False", "quote": false[:300]}],
                "rationale": f"Snopes 明确指出哪一部分为假（评级「{rating}」）。"}
        elif fam == "media":
            about = sn["sections"].get("About this rating", "")
            layers["claim_layer"] = {
                "verdict": "source_says_false",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "About this rating",
                              "quote": (about or f"Rating: {rating}")[:300]}],
                "rationale": f"核查源判为「{rating}」：素材/标注与所述不符，属表述层问题。"}
        elif fam == "false":
            layers["claim_layer"] = {
                "verdict": "source_says_false",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "Rating", "quote": f"Rating: {rating}"}],
                "rationale": f"Snopes 评级「{rating}」，整条主张不成立。"}
        elif undet:
            layers["claim_layer"] = {
                "verdict": "source_mentions",
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "What's Undetermined", "quote": undet[:300]}],
                "rationale": f"Snopes 评级「{rating}」，尚有未确定部分。"}

        # 素材层：Snopes 原文里的画面类措辞
        mk = media_markers(m.get("body", ""))
        if mk:
            layers["media_layer"] = {
                "verdict": "source_says_media_mismatch",
                "kind": mk[0]["kind"],
                "evidence": [{"case_id": m["case_id"], "source": "snopes",
                              "field": "body", "quote": mk[0]["quote"]}],
                "rationale": "核查原文点名画面/素材问题（" + mk[0]["kind"] + "）。"}
        if layers["event_layer"]["verdict"] != "insufficient" and \
                layers["claim_layer"]["verdict"] != "insufficient":
            break

    # ---- 中文辟谣站：只作"来源提及"，不冒充结构化判定
    if layers["event_layer"]["verdict"] == "insufficient":
        for m in fact:
            if m.get("platform") != "piyao":
                continue
            sig = piyao_signals(m.get("body", ""))
            if sig:
                layers["event_layer"] = {
                    "verdict": "source_mentions",
                    "evidence": [{"case_id": m["case_id"], "source": "piyao",
                                  "field": "body", "quote": sig[0]["quote"]}],
                    "rationale": "中文辟谣站出现点名该主张的表述，但站点未给结构化评级。"}
                break

    # ---- 主张层补强：没有核查源时，至少把"强断言"标出来（只提示，不定性）
    if layers["claim_layer"]["verdict"] == "insufficient":
        strong = []
        for m in social + fact:
            w = strong_claims(m.get("title", ""))
            if w:
                strong.append({"case_id": m["case_id"], "words": w,
                               "title": (m.get("title") or "")[:120]})
        if strong:
            layers["claim_layer"] = {
                "verdict": "insufficient",
                "evidence": [{"case_id": s["case_id"], "source": "claim_text",
                              "field": "title", "quote": s["title"]}
                             for s in strong[:2]],
                "rationale": ("主张含强断言词（" + "、".join(strong[0]["words"]) +
                              "），但无核查源原文支撑，表述层需人工/外部检索判定。"),
                "strong_terms": strong[0]["words"]}

    return layers


def summarize(layers: dict) -> dict:
    """给卡片/统计用的紧凑视图。"""
    return {k: {"verdict": v["verdict"], "rationale": v["rationale"],
                "quotes": [e.get("quote", "")[:120] for e in v.get("evidence", [])]}
            for k, v in layers.items()}


if __name__ == "__main__":
    # 自检：拿仓库里两个真实案例跑一遍解析（不联网）
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    ds = Path(__file__).resolve().parents[1] / "dataset"
    for name in ("factcheck__snopes_005", "factcheck__snopes_002", "factcheck__piyao_007"):
        body = (ds / name / "text" / "text.txt").read_text(encoding="utf-8", errors="replace")
        members = [{"case_id": name, "platform": name.split("__")[0].replace("factcheck", "")
                    .strip("_") or ("snopes" if "snopes" in name else "piyao"),
                    "title": body.splitlines()[0][:80], "body": body,
                    "parsed": parse_snopes(body)}]
        layers = judge_layers(members)
        print("=" * 24, name)
        for k, v in summarize(layers).items():
            print(f"  {k:12} {v['verdict']:26} {v['rationale'][:60]}")
            if v["quotes"]:
                print(f"  {'':12} ↳ {v['quotes'][0][:100]}")
