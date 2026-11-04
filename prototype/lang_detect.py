# -*- coding: utf-8 -*-
"""轻量语种探测（lang 提示字段，不作权威判定）。

设计取舍：
- 文字系统优先：CJK 占比 ≥15% 判 zh；卡纳达文 ≥10% 判 kn（试点集 X 有卡纳达语推文）；
- 拉丁文用虚词投票区分 en / pt / id（印尼语与葡语同为拉丁字母，虚词区分最便宜）；
- 全程零依赖，不装 langdetect；拉丁文无信号时默认 en（试点集英推占多数）。
- 判定可被人工覆盖：index 里手改 lang 后，本模块不会回写（backfill 只填空值）。
"""
from __future__ import annotations

import re

_EN_STOPS = (" the ", " and ", " of ", " to ", " is ", " that ", " for ",
             " with ", " this ", " was ")
_ES_STOPS = (" el ", " los ", " las ", " una ", " uno ", " pero ", " este ",
             " esta ", " está ", " como ", " que ", " de ", " para ")
_PT_STOPS = (" de ", " que ", " não ", " para ", " uma ", " dos ", " das ",
             " até ", " pelo ", " como ", " mas ", " seu ", " sua ")
_ID_STOPS = (" yang ", " dan ", " untuk ", " dengan ", " ini ", " itu ",
             " tidak ", " karena ", " sudah ", " akan ")


def detect_lang(text: str) -> str:
    """返回 zh / kn / en / pt / id / und（und = 无文本）。"""
    raw = (text or "").strip()
    if not raw:
        return "und"
    n = max(len(raw.replace(" ", "")), 1)
    cjk = sum(1 for ch in raw if "\u4e00" <= ch <= "\u9fff") / n
    if cjk >= 0.15:
        return "zh"
    kn = sum(1 for ch in raw if "\u0c85" <= ch <= "\u0cb9") / n
    if kn >= 0.10:
        return "kn"
    s = " " + re.sub(r"[^\w\s]", " ", raw.lower()) + " "
    votes = {"en": 0, "es": 0, "pt": 0, "id": 0}
    for w in _EN_STOPS:
        votes["en"] += s.count(w)
    for w in _ES_STOPS:
        votes["es"] += s.count(w)
    for w in _PT_STOPS:
        votes["pt"] += s.count(w)
    for w in _ID_STOPS:
        votes["id"] += s.count(w)
    low = raw.lower()
    if "ñ" in low:                 # ñ 是西语独有字母
        return "es"
    if "ção" in low or "não" in low or "ões" in low:  # 葡语标志性词形
        return "pt"
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] > 0 else "en"
