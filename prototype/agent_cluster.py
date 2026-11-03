# -*- coding: utf-8 -*-
"""归簇 Agent：把案例按主张相似度并成事件簇。

要解决的问题（实战里验证过的那个）：
  同一则谣言会被切成很多条——不同平台的转述、不同账号上传的同一段视频。
  不归簇就会把"一条谣"数成"十条谣"，标注量和评测分母都是虚高的。

算法（每条选择都对着实测数据调过，理由写在注释里，方便写进论文）：
  1. **词袋 + 语料自算 IDF**：不引外部语料，IDF 由这批案例自身算，
     专名权重自然高、套话权重自然低。
  2. **中文用字符 bigram**：零依赖不装分词器，对未登录专名免疫。
  3. **标题 ×3 加权**：正文（YouTube 是整段字幕）会稀释标题信号，
     而"同一主张"的判断主要发生在标题层。
  4. **cos 与包含度各半（0.6cos + 0.4contain）**：cos 对长度敏感，
     推特短帖 vs YouTube 长字幕的余弦会被系统性压低；
     包含度（|A∩B|/min 权重和）正好补这一侧。
  5. **average-linkage 凝聚聚类**：不用 single-linkage，后者会"链式拉链"，
     一条居中文本能把两个不相干事件接通。
  6. **默认阈值 0.45（精度优先）**：实测 97 个案例上，0.30 会给出 3 次合并但含 2 次错并
     （见 events.json 的 pair_curve）；0.45 只合并 1 对，且是真正的同一事件。
     **诚实边界**：词面相似度只能抓"近乎同文"的重复（跨平台搬运、同视频多号上传），
     抓不了"同一事件的不同说法"——那需要实体/时间/地点归一，属于下一版。
     所以本 Agent 的输出是**保守下界**：合并的可以信，没合并的不代表不同事件。

输出 metadata/events.json（events.v1）：成员、平台分布、主张变体、标签一致性、
簇内 top 词、以及**合并证据**（每次合并记录 a/b/相似度）供人工复核。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from common import METADATA_DIR, ROOT

DATASET_DIR = ROOT / "dataset"
INDEX = DATASET_DIR / "index.json"
OUT = METADATA_DIR / "events.json"

STOP_EN = set("""the a an and or of to in for on is are was were be been being it its this that these those
with without from as at by not no do does did can could should would will shall may might must
how what why who whom which when where vs versus you your we our they their he she his her
new news video watch live full report breaking update today now says said say claim claims
fake real true false about after before over under more most less than then there here
one two three first last get got make made use used also just like very much many""".split())
CJK = r"\u4e00-\u9fff"
RE_URL = re.compile(r"https?://\S+")
RE_TOKEN = re.compile(rf"[a-z][a-z0-9']+|[0-9]{{3,}}|[{CJK}]+")

TITLE_BOOST = 3          # 标题 token 重复次数（相当于字段加权）
ALPHA = 0.6              # sim = ALPHA*cos + (1-ALPHA)*containment
DEFAULT_THRESHOLD = 0.45
CURVE = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)


def tokenize(text: str) -> list[str]:
    """英词 + 数字 + 中文字符 bigram；返回保留重复的 token 序列（供 TF 用）。"""
    text = RE_URL.sub(" ", (text or "").lower())
    out: list[str] = []
    for m in RE_TOKEN.finditer(text):
        tok = m.group(0)
        if tok[0].isascii():
            if tok[0].isalpha():
                if len(tok) >= 3 and tok not in STOP_EN:
                    out.append(tok)
            elif len(tok) >= 3:          # 两位数字太常见，纯噪声
                out.append("#" + tok)
        else:
            if len(tok) == 1:
                continue
            for i in range(len(tok) - 1):  # 中文 bigram
                out.append(tok[i:i + 2])
    return out


def case_tokens(c: dict) -> Counter:
    """标题（×3）+ 正文（截断 1200 字）的 token 计数。"""
    tf = Counter(tokenize(c.get("title") or "") * TITLE_BOOST)
    paths = [p for p in c.get("files", {}).get("text", []) if p.endswith("text.txt")]
    if paths:
        p = DATASET_DIR / paths[0]
        if p.exists():
            body = p.read_text(encoding="utf-8", errors="replace")
            body = re.sub(r"^#.*$", "", body, flags=re.M)
            tf.update(tokenize(body[:1200]))
    return tf


def load_cases(source: str = "index") -> list[dict]:
    """source=index 读 dataset/index.json；=sweep 直接扫 dataset/*/ 目录。"""
    if source == "index" and INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8")).get("cases", [])
    cases = []
    for d in (sorted(DATASET_DIR.iterdir()) if DATASET_DIR.exists() else []):
        if not d.is_dir():
            continue
        p = d / "text" / "text.txt"
        cases.append({"id": d.name, "case_dir": d.name, "title": d.name,
                      "platform": "unknown", "topic": None, "label": None,
                      "files": {"text": [f"{d.name}/text/text.txt"]} if p.exists() else {}})
    return cases


def build_vectors(tf_lists: list[Counter]) -> list[dict[str, float]]:
    """TF-IDF（IDF 由本语料自算，TF 取 1+log），并做 L2 归一化。"""
    n = len(tf_lists)
    df = Counter()
    for tf in tf_lists:
        df.update(tf.keys())
    idf = {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}
    vecs = []
    for tf in tf_lists:
        v = {t: (1.0 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    return vecs


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def containment(a: dict, b: dict) -> float:
    """包含度：小向量被大向量覆盖的比例（对长度差鲁棒）。"""
    if len(a) > len(b):
        a, b = b, a
    if not a:
        return 0.0
    inter = sum(min(w, b[t]) for t, w in a.items() if t in b)
    return inter / (sum(a.values()) or 1.0)


def similarity(a: dict, b: dict) -> float:
    return ALPHA * cosine(a, b) + (1 - ALPHA) * containment(a, b)


def pair_matrix(vecs: list[dict]) -> dict[tuple[int, int], float]:
    sim: dict[tuple[int, int], float] = {}
    n = len(vecs)
    for i in range(n):
        for j in range(i + 1, n):
            s = similarity(vecs[i], vecs[j])
            if s > 0.02:
                sim[(i, j)] = s
    return sim


def _get(sim: dict, i: int, j: int) -> float:
    return sim.get((i, j) if i < j else (j, i), 0.0)


def cluster(sim: dict, n: int, threshold: float, max_size: int,
            topic_prior: float = 0.0, topics: list[str] | None = None):
    """average-linkage 凝聚聚类，返回 (簇列表, 合并证据)。"""
    def adj(i, j):
        s = _get(sim, i, j)
        if topic_prior and topics and topics[i] and topics[i] == topics[j]:
            s += topic_prior
        return s

    clusters: list[list[int]] = [[i] for i in range(n)]
    merges: list[dict] = []
    while True:
        best, pair = threshold, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                A, B = clusters[a], clusters[b]
                if len(A) + len(B) > max_size:
                    continue
                tot = sum(adj(i, j) for i in A for j in B)
                avg = tot / (len(A) * len(B))
                if avg > best:
                    best, pair = avg, (a, b)
        if not pair:
            break
        a, b = pair
        A, B = clusters[a], clusters[b]
        # 记录最强的一对成员作为"合并理由"（人工复核时直接看这两条）
        top = max(((adj(i, j), i, j) for i in A for j in B), default=(0.0, A[0], B[0]))
        merges.append({"avg_sim": round(best, 3), "strongest": round(top[0], 3),
                       "a": top[1], "b": top[2], "sizes": [len(A), len(B)]})
        merged = A + B
        clusters = [c for k, c in enumerate(clusters) if k not in (a, b)] + [merged]
    return sorted(clusters, key=len, reverse=True), merges


def top_tokens(vecs: list[dict], members: list[int], k: int = 8) -> list[str]:
    agg: dict[str, float] = defaultdict(float)
    for i in members:
        for t, w in vecs[i].items():
            agg[t] += w
    return [t for t, _ in sorted(agg.items(), key=lambda kv: -kv[1])[:k]]


def run(params: dict) -> dict:
    threshold = float(params.get("threshold") or DEFAULT_THRESHOLD)
    max_size = int(params.get("max_size") or 60)
    source = params.get("source") or "index"

    cases = load_cases(source)
    if not cases:
        return {"status": "error", "error": "没有可聚类的案例（dataset/index.json 为空？）"}

    vecs = build_vectors([case_tokens(c) for c in cases])
    sim = pair_matrix(vecs)
    topics = [c.get("topic") or "" for c in cases]
    groups, merges = cluster(sim, len(cases), threshold, max_size,
                             float(params.get("topic_prior") or 0.0), topics)

    events = []
    for rank, g in enumerate(groups, 1):
        members = [cases[i] for i in g]
        plat = Counter(m.get("platform") or "unknown" for m in members)
        labs = Counter(str(m.get("label")) for m in members)
        claims, seen = [], set()
        for m in members:
            t = (m.get("title") or "").strip()
            key = t.lower()[:60]
            if key and key not in seen:
                seen.add(key)
                claims.append({"case_id": m.get("id"), "title": t[:140],
                               "platform": m.get("platform"), "label": m.get("label")})
        events.append({
            "event_id": f"E{rank:02d}",
            "size": len(members),
            "platforms": dict(plat),
            "multi_platform": len(plat) >= 2,
            "labels": dict(labs),
            "label_present": sum(v for k, v in labs.items() if k not in ("None", "null")),
            "claim_variants": len(claims),
            "claims": claims[:12],
            "top_tokens": top_tokens(vecs, g),
            "case_ids": [m.get("id") for m in members],
            "topics": sorted({m.get("topic") for m in members if m.get("topic")}),
        })

    # 阈值-产量曲线：把"为什么是这个阈值"变成可复现的数字，而不是一句话结论
    pair_curve = {f"{t:.2f}": sum(1 for s in sim.values() if s >= t) for t in CURVE}
    payload = {
        "schema_version": "events.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "params": {"threshold": threshold, "max_size": max_size,
                   "alpha": ALPHA, "title_boost": TITLE_BOOST},
        "case_count": len(cases),
        "count": len(events),
        "singletons": sum(1 for e in events if e["size"] == 1),
        "reduction": round(1 - len(events) / len(cases), 3) if cases else 0.0,
        "pairs_above_threshold": sum(1 for s in sim.values() if s >= threshold),
        "pair_curve": pair_curve,
        "merges": [{"avg_sim": m["avg_sim"], "strongest": m["strongest"],
                    "pair": [cases[m["a"]]["id"], cases[m["b"]]["id"]],
                    "sizes": m["sizes"]} for m in merges],
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "status": "ok",
        "outputs": {"path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
                    "case_count": len(cases), "event_count": len(events),
                    "reduction": payload["reduction"],
                    "singletons": payload["singletons"],
                    "merges": len(merges),
                    "note": "词面相似度只抓近乎同文的重复，合并结果可当保守下界"},
        "artifacts": [str(OUT.relative_to(ROOT)).replace("\\", "/")],
        "metrics": {"cases": len(cases), "events": len(events),
                    "largest": events[0]["size"] if events else 0},
        "next": ["verify"],
    }
