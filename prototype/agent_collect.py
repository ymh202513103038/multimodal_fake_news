# -*- coding: utf-8 -*-
"""采集 Agent：把已就绪的四个采集通道包成统一动作。

本 Agent 不自带采集逻辑——它是**编排适配器**：把 (action, params) 翻译成
`prototype/` 下既有脚本的命令行，执行后统计"落盘前后行数差"作为产出度量。

为什么不自带逻辑：四个通道都是实测过的独立脚本（配额账本、断点续跑、
零凭据浏览器等细节都在里面），复制一份到 Agent 里会立刻产生两套行为。
适配器模式保证 Agent 与命令行永远同源。

度量口径（诚实标注）：
  added  = 目标文件行数增量（不是"新增独立事件数"，重采会重复计数）
  dedup  = 目标文件按主键去重后条目数（脚本自身幂等去重的结果）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from common import LOGS_DIR, METADATA_DIR, ROOT

PY = sys.executable
PROTO = ROOT / "prototype"

# action -> (脚本名, 参数到 CLI 的映射, 度量目标)
# 度量目标：("file", Path) / ("dir", Path) / ("glob", Path, pattern)
ROUTES: dict[str, dict] = {
    "youtube_search": {
        "script": "step3_youtube_probe.py",
        "argmap": {"query": "--query", "limit": "--limit", "api_key": "--api-key"},
        "flags": {"mock": "--mock", "fresh": "--fresh"},
        "watch": ("file", METADATA_DIR / "youtube_meta.jsonl", "video_id"),
    },
    "youtube_harvest": {
        "script": "harvest_daily.py",
        "argmap": {"queries": "--queries", "limit": "--limit"},
        "flags": {"dry_run": "--dry-run"},
        "watch": ("file", METADATA_DIR / "youtube_meta.jsonl", "video_id"),
    },
    "youtube_media": {
        "script": "step4_youtube_media.py",
        "argmap": {"what": "--what", "limit": "--limit", "vids": "--vids"},
        "flags": {"fresh": "--fresh"},
        "watch": ("file", METADATA_DIR / "youtube_media.jsonl", "video_id"),
    },
    "x_browser": {
        "script": "x_browser_probe.py",
        "argmap": {"keyword": "--keyword", "rounds": "--rounds", "limit": "--limit"},
        "flags": {},
        "watch": ("dir", METADATA_DIR / "x", "tweet_id"),
    },
    "news": {
        "script": "news_probe.py",
        "argmap": {"site": "--site", "limit": "--limit"},
        "flags": {},
        "watch": ("dir", METADATA_DIR / "news", None),
    },
    "rebuild_cases": {
        "script": "make_cases.py",
        "argmap": {},
        "flags": {"fresh": "--fresh"},
        "watch": ("index", ROOT / "dataset" / "index.json", None),
    },
}

# 案例层重建的度量要读 index.json 的 count（一行 JSON，不是 jsonl 行数）
INDEX_JSON = ROOT / "dataset" / "index.json"


def _index_count() -> int:
    try:
        return int(json.loads(INDEX_JSON.read_text(encoding="utf-8")).get("count") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _watch_stats(spec) -> tuple[int, int]:
    """返回 (总行数, 去重后条目数)；cases 层则返回 (案例数, 案例数)。"""
    kind = spec[0]
    if kind == "index":
        n = _index_count()
        return n, n
    path = spec[1]
    id_field = spec[2] if len(spec) > 2 else None
    files: list[Path] = []
    if kind == "file":
        files = [path] if path.exists() else []
    else:
        files = sorted(path.rglob("*.jsonl")) if path.exists() else []
    lines = 0
    ids = set()
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                lines += 1
                if id_field:
                    try:
                        rec = json.loads(ln)
                        ids.add(rec.get(id_field) or rec.get("id") or ln[:120])
                    except json.JSONDecodeError:
                        ids.add(ln[:120])
    return lines, (len(ids) if id_field else 0)


def _to_bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------------- 案例层重建
#
# 为什么这一环需要"回归守卫"（实测踩到的坑，不是假设）：
#   make_cases 只从 metadata/*.jsonl 重建 dataset/，而 factcheck 案例的正文是
#   后续由 patch_factcheck_bodies.py / enrich_bodies.py **联网补抓**写进 text.txt 的。
#   直接重跑 make_cases 会把补抓的正文覆盖回"只有标题"，数据静默变差——
#   第一次跑就丢了 30 个案例的正文（100 个文件被改，1990 行删除）。
#   所以：重建 = make_cases + 补抓链 + **逐文件比对回滚**。
#   补抓失败（无网/被限流）时，守卫保证正文不退化，只在结果里报出来。

ENRICH_CHAIN = ("patch_factcheck_bodies.py", "enrich_bodies.py")
DS = ROOT / "dataset"


def _snapshot_texts() -> dict[str, str]:
    """快照所有案例正文与来源文件（体积小：纯文本）。"""
    snap: dict[str, str] = {}
    if not DS.exists():
        return snap
    for p in DS.glob("*/*/*.txt"):
        if p.parent.name in ("text", "web", "reason"):
            try:
                snap[str(p.relative_to(ROOT)).replace("\\", "/")] = \
                    p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return snap


def _restore_regressions(snap: dict[str, str]) -> list[dict]:
    """重建后逐文件比对：正文变短（或消失）就写回旧内容，并记录。"""
    bad = []
    for rel, old in snap.items():
        p = ROOT / rel
        new = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        if len(new.strip()) < len(old.strip()) * 0.9 and len(old.strip()) > 80:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(old, encoding="utf-8")
            bad.append({"path": rel, "before": len(old.strip()),
                        "after": len(new.strip())})
    return bad


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _run_rebuild(params) -> dict:
    timeout = int(params.get("timeout_s") or 1800)
    do_enrich = params.get("enrich") is None or _to_bool(params.get("enrich"))
    before = _index_count()
    snap = _snapshot_texts()
    steps = []

    cmd = build_cmd("rebuild_cases", params)
    if _to_bool(params.get("dry_run", False)):
        return {"status": "ok", "outputs": {"dry_run": True, "cmd": " ".join(cmd)},
                "metrics": {"executed": False}, "next": ["cluster"]}
    try:
        p = _run(cmd, timeout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"make_cases timeout>{timeout}s"}
    steps.append({"step": "make_cases", "exit": p.returncode})
    if p.returncode != 0:
        sl = [ln.strip() for ln in (p.stderr or "").splitlines() if ln.strip()]
        return {"status": "error",
                "error": f"make_cases exit={p.returncode}"
                         + (f"｜{sl[-1][:200]}" if sl else ""),
                "outputs": {"stderr": (p.stderr or "")[-800:]}}

    if do_enrich:
        for script in ENRICH_CHAIN:
            sp = PROTO / script
            if not sp.exists():
                steps.append({"step": script, "skipped": "missing"})
                continue
            try:
                pe = _run([PY, str(sp)], timeout)
                steps.append({"step": script, "exit": pe.returncode})
            except subprocess.TimeoutExpired:
                steps.append({"step": script, "error": "timeout"})

    restored = _restore_regressions(snap)
    after = _index_count()
    return {
        "status": "ok",
        "outputs": {
            "cases_before": before, "cases_after": after,
            "steps": steps,
            "regressions_restored": len(restored),
            "restored_sample": [r["path"] for r in restored[:5]],
            "note": ("补抓失败时正文已自动回滚，不会静默变差"
                     if restored else "无正文回归"),
        },
        "artifacts": ["dataset/index.json"],
        "metrics": {"cases": after, "regressions": len(restored)},
        "next": ["cluster"],
    }


def build_cmd(action: str, params: dict) -> list[str]:
    r = ROUTES[action]
    cmd = [PY, str(PROTO / r["script"])]
    for k, flag in r["argmap"].items():
        v = params.get(k)
        if v is None or v == "":
            continue
        cmd += [flag, str(v)]
    for k, flag in r["flags"].items():
        if k in params and _to_bool(params[k]):
            cmd.append(flag)
    return cmd


def run(action: str, params: dict) -> dict:
    """执行采集动作，返回 {outputs, artifacts, metrics} 片段。"""
    if action not in ROUTES:
        return {"status": "error", "error": f"collect 无动作 {action}"}
    if action == "rebuild_cases":
        return _run_rebuild(params)

    cmd = build_cmd(action, params)
    if _to_bool(params.get("dry_run", False)):
        return {"status": "ok",
                "outputs": {"dry_run": True, "cmd": " ".join(cmd)},
                "metrics": {"executed": False},
                "next": ["cluster"]}

    spec = ROUTES[action]["watch"]
    before_lines, before_ids = _watch_stats(spec)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timeout = int(params.get("timeout_s") or 1800)
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"timeout>{timeout}s",
                "outputs": {"cmd": " ".join(cmd)}}

    after_lines, after_ids = _watch_stats(spec)
    tail = (p.stdout or "")[-1200:]
    metrics = {"added": after_lines - before_lines,
               "deduped": after_ids,
               "prior": before_ids,
               "exit": p.returncode}
    if p.returncode != 0:
        # 把 stderr 最后一两行拼进 error：主控只记 error 字段，翻 outputs 太费事
        sl = [ln.strip() for ln in (p.stderr or "").splitlines() if ln.strip()]
        return {"status": "error",
                "error": f"exit={p.returncode}" + (f"｜{sl[-1][:200]}" if sl else ""),
                "outputs": {"cmd": " ".join(cmd),
                            "stderr": (p.stderr or "")[-800:]},
                "metrics": metrics}
    if metrics["added"] <= 0 and not _to_bool(params.get("allow_empty", False)):
        # 0 新增不是硬错误，但要显式报出来：多半是登录态/代理/配额问题
        return {"status": "ok",
                "outputs": {"cmd": " ".join(cmd), "note": "本次 0 新增（可能已去重或通道不可用）",
                            "stdout_tail": tail},
                "metrics": metrics, "next": []}
    return {"status": "ok",
            "outputs": {"cmd": " ".join(cmd), "stdout_tail": tail},
            "artifacts": [str(spec[1].relative_to(ROOT)).replace("\\", "/")],
            "metrics": metrics,
            "next": ["cluster"]}
