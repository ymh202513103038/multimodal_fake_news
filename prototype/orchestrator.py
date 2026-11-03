# -*- coding: utf-8 -*-
"""调度主控（第 4 步）：任务队列 + 路由 + 退避重试 + 依赖编排。

定位：这是项目里**唯一之前没做的一环**——工具层（采集四通道、归簇、核查、出卡）
全是现成的，缺的是"谁来按什么顺序调、失败怎么办"。

设计要点（每条都有具体理由，不是通用架构套话）：

1. **主控与 Agent 之间只走 agent.v1 契约**（见 agent_protocol.py）。
   好处是主控可以换成 LLM 而不动工具：把 `manifest` 子命令的输出丢给模型当
   tool schema，模型产出 {agent, action, params}，主控照着跑即可。
   本文件的规则状态机是"最保守的主控基线"，LLM 主控是它的增强版，两者同源。

2. **状态落盘可断点续跑**（metadata/tasks.json）。采集会被 429／代理抖动／
   登录态打断，跑一半的任务队列必须能重启接着走——这是实战里最先要用到的能力。

3. **指数退避 + 连续失败封顶**：429/超时按 30/60/120 秒退避；同一任务连续
   3 次失败 → `paused`，不硬刚（X 通道连续 401 还硬跑会被风控，见
   documentation/compliance_checklist.md）。

4. **依赖失败要"传染"**：上游 paused 后，下游从 pending 变 blocked，
   而不是永远 pending 假装还有希望——否则 status 会显示"全部待办"骗人。

用法（仓库根目录）：
  python prototype/orchestrator.py manifest            # 打印动作清单（可喂 LLM）
  python prototype/orchestrator.py plan --channels youtube,x --pipeline
  python prototype/orchestrator.py run --dry           # 只看要执行什么
  python prototype/orchestrator.py run                 # 真跑
  python prototype/orchestrator.py status
  python prototype/orchestrator.py plan --channels full --pipeline --reset
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_protocol import (ACTION_MANIFEST, ROOT, make_task, manifest_json,
                            now_iso)

METADATA = ROOT / "metadata"
LOGS = ROOT / "logs"
QUEUE = METADATA / "tasks.json"
RUNS = METADATA / "runs.jsonl"
LOG = LOGS / "orchestrator.log"
SCHEMA = "tasks.v1"
STATUSES = ("pending", "running", "done", "error", "paused", "blocked", "cancelled")

DEFAULT_BACKOFF = [30, 60, 120]
MAX_RETRIES = 3


# ---------------------------------------------------------------- 队列读写

def load_queue() -> dict:
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    return {"schema_version": SCHEMA, "updated_at": now_iso(), "tasks": []}


def save_queue(q: dict) -> None:
    q["updated_at"] = now_iso()
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(QUEUE)


def log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} {msg}"
    print(msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_run(rec: dict) -> None:
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 入队

def enqueue(q: dict, agent: str, action: str, params: dict,
            depends_on: list[str] | None = None, note: str = "") -> dict:
    t = make_task(agent, action, params)
    t.update({"status": "pending", "retries": 0, "depends_on": depends_on or [],
              "next_attempt_at": None, "note": note,
              "history": []})
    q["tasks"].append(t)
    return t


def build_plan(channels: list[str], args) -> list[dict]:
    """把一个采集意图展开成任务链（采集 → 归簇 → 核查 → 出卡）。"""
    q = load_queue()
    created: list[dict] = []

    for ch in channels:
        if ch == "none":
            # 显式"不采集"：只挂流水线（归簇→核查→出卡），用于对已落盘数据重跑
            continue
        if ch == "youtube":
            created.append(enqueue(q, "collect", "youtube_search",
                                   {"query": args.yt_query, "limit": args.yt_limit,
                                    "mock": bool(args.mock)},
                                   note="YouTube Data API 关键词搜索"
                                        + ("（mock：不调 API）" if args.mock else "")))
        elif ch == "yt-harvest":
            created.append(enqueue(q, "collect", "youtube_harvest",
                                   {"queries": args.yt_queries, "limit": args.yt_limit},
                                   note="每日轮转收割（读断点）"))
        elif ch == "x":
            created.append(enqueue(q, "collect", "x_browser",
                                   {"keyword": args.x_keyword, "rounds": args.x_rounds,
                                    "limit": args.x_limit},
                                   note="零凭据真浏览器（需 Chrome 已登录 x.com）"))
        elif ch == "news":
            created.append(enqueue(q, "collect", "news", {"site": args.news_site,
                                                          "limit": args.news_limit},
                                   note="核查站模板采集"))
        else:
            raise SystemExit(f"未知通道 {ch}（可选 youtube / yt-harvest / x / news）")

    if args.pipeline:
        deps = [t["task_id"] for t in created]
        # 采集与归簇之间必须过一遍案例层组装：归簇读的是 dataset/index.json，
        # 缺这一步，新采的数据永远进不了下游（最容易漏的一环）。
        if deps:
            rb = enqueue(q, "collect", "rebuild_cases", {"fresh": False},
                         depends_on=deps, note="案例层组装：原始层 → dataset/")
            created.append(rb)
            deps = [rb["task_id"]]
        c = enqueue(q, "cluster", "cluster_cases",
                    {"threshold": args.threshold}, depends_on=deps,
                    note="归簇：一事一簇，抑制一谣多计")
        v = enqueue(q, "verify", "build_packets", {}, depends_on=[c["task_id"]],
                    note="三层判定工作表 + 缺口预检（不判真假）")
        enqueue(q, "card", "make_card",
                {"event_id": "all", "limit": args.cards}, depends_on=[v["task_id"]],
                note="渲染案例卡")
        created += [c, v]

    save_queue(q)
    return created


# ---------------------------------------------------------------- 调度

def _ready(q: dict, assume_done: frozenset[str] = frozenset()) -> list[dict]:
    """可执行任务：pending + 依赖全 done + 退避到点。依赖失败则把下游标 blocked。

    assume_done 供演练模式用：把"已演练过"的任务当作 done，
    这样 --dry 能把整条链（采集→归簇→核查→出卡）都走一遍给人看，而不是只看队首。
    """
    by_id = {t["task_id"]: t for t in q["tasks"]}
    now = datetime.now(timezone.utc)
    ready = []
    for t in q["tasks"]:
        if t["status"] != "pending":
            continue
        deps = [by_id[d] for d in t.get("depends_on", []) if d in by_id]
        if any(d["status"] in ("paused", "cancelled", "blocked") for d in deps):
            t["status"] = "blocked"
            t["note"] = (t.get("note") or "") + " [阻塞：上游 paused/blocked]"
            continue
        if any(d["status"] != "done" and d["task_id"] not in assume_done for d in deps):
            continue
        na = t.get("next_attempt_at")
        if na and datetime.fromisoformat(na) > now:
            continue
        ready.append(t)
    return ready


def run_task(t: dict, python: str, dry: bool) -> dict:
    if dry:
        return {"status": "ok", "outputs": {"dry_run": True,
                                           "cmd_agent": f"{t['agent']}/{t['action']}",
                                           "params": t.get("params")}}
    from agent_protocol import run_agent
    return run_agent(t, python=python, timeout_s=int(t.get("timeout_s") or 900))


def cmd_run(args) -> int:
    q = load_queue()
    backoff = [float(x) for x in str(args.backoff).split(",") if x.strip()]
    if not backoff:
        backoff = list(DEFAULT_BACKOFF)
    py = args.python or sys.executable
    executed = 0
    skip: set[str] = set()   # 演练模式已"走"过的任务，避免原地打转

    while True:
        ready = [t for t in _ready(q, frozenset(skip)) if t["task_id"] not in skip]
        save_queue(q)  # _ready 可能把任务标 blocked，先落盘
        if not ready:
            break
        if args.limit and executed >= args.limit:
            log(f"达到本次上限 {args.limit}，剩余 {len(ready)} 个待执行")
            break
        t = ready[0]
        executed += 1
        t["history"] = t.get("history") or []
        t["status"] = "running"
        t["started_at"] = now_iso()
        save_queue(q)

        log(f"▶ {t['task_id']}  {t['agent']}/{t['action']}  "
            f"{json.dumps(t.get('params'), ensure_ascii=False)}")
        res = run_task(t, py, args.dry)
        status = res.get("status", "error")
        append_run({"at": now_iso(), "task_id": t["task_id"], "agent": t["agent"],
                    "action": t["action"], "status": status,
                    "metrics": res.get("metrics"), "error": res.get("error"),
                    "elapsed_s": res.get("elapsed_s"), "dry": bool(args.dry)})

        if args.dry:
            # 演练：记录计划、不改状态、不睡觉、不重试
            skip.add(t["task_id"])
            t["status"] = "pending"
            t["history"].append({"at": now_iso(), "status": "dry_run", "error": None})
            log(f"  · 演练 {t['task_id']}（would run {t['agent']}/{t['action']}）")
            save_queue(q)
            continue

        if status == "ok":
            t["status"] = "done"
            t["finished_at"] = now_iso()
            t["result"] = {"outputs": res.get("outputs"),
                           "artifacts": res.get("artifacts"),
                           "metrics": res.get("metrics")}
            t["next"] = res.get("next") or []
            log(f"  ✔ {t['task_id']}  "
                f"{json.dumps(res.get('outputs') or {}, ensure_ascii=False)[:300]}")
        else:
            t["retries"] = int(t.get("retries") or 0) + 1
            t["last_error"] = res.get("error")
            if t["retries"] >= int(args.max_retries):
                t["status"] = "paused"
                log(f"  ✖ {t['task_id']} 连续失败 {t['retries']} 次 → paused："
                    f"{res.get('error')}")
            else:
                wait = backoff[min(t["retries"] - 1, len(backoff) - 1)]
                t["status"] = "pending"
                t["next_attempt_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=wait)
                ).isoformat(timespec="seconds")
                log(f"  ✖ {t['task_id']} 失败（第 {t['retries']} 次）："
                    f"{res.get('error')} → {wait:.0f}s 后重试")
                time.sleep(wait)
        t["history"].append({"at": now_iso(), "status": status,
                             "error": res.get("error")})
        save_queue(q)

    summary = _summary(load_queue())
    log("—" * 46)
    log(f"队列状态：{json.dumps(summary, ensure_ascii=False)}")
    return 0 if summary.get("paused", 0) == 0 else 1


def _summary(q: dict) -> dict:
    s = {k: 0 for k in STATUSES}
    for t in q["tasks"]:
        s[t["status"]] = s.get(t["status"], 0) + 1
    s["total"] = len(q["tasks"])
    return s


def cmd_status(args) -> int:
    q = load_queue()
    print(f"队列：{QUEUE}（{len(q['tasks'])} 个任务，更新于 {q.get('updated_at')}）")
    print(f"{'task_id':32} {'agent/action':28} {'status':9} {'retries':7} depends_on")
    print("-" * 100)
    for t in q["tasks"]:
        print(f"{t['task_id']:32} {t['agent'] + '/' + t['action']:28} "
              f"{t['status']:9} {t.get('retries', 0):<7} "
              f"{','.join(t.get('depends_on') or [])[:30]}")
        if t.get("last_error"):
            print(f"{'':33}↳ {str(t['last_error'])[:70]}")
    print("-" * 100)
    print(json.dumps(_summary(q), ensure_ascii=False))
    return 0


def cmd_plan(args) -> int:
    if args.reset:
        old = QUEUE.with_suffix(".bak.json")
        if QUEUE.exists():
            QUEUE.replace(old)
            print(f"旧队列已备份到 {old.name}")
        q = {"schema_version": SCHEMA, "updated_at": now_iso(), "tasks": []}
        save_queue(q)
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    created = build_plan(channels, args)
    print(f"已入队 {len(created)} 个任务：")
    for t in created:
        print(f"  {t['task_id']}  {t['agent']}/{t['action']}  "
              f"deps={len(t.get('depends_on') or [])}  {t.get('note', '')}")
    print("\n下一步：python prototype/orchestrator.py run --dry  # 先演练")
    return 0


def cmd_submit(args) -> int:
    q = load_queue()
    params = json.loads(args.params) if args.params else {}
    t = enqueue(q, args.agent, args.action, params,
                [s for s in (args.depends or "").split(",") if s])
    save_queue(q)
    print(f"已入队 {t['task_id']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="真途调度主控")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("manifest", help="打印四个 Agent 的动作清单（可喂 LLM 当 tool schema）")
    sp.set_defaults(func=lambda a: (print(manifest_json()), 0)[1])

    sp = sub.add_parser("plan", help="展开采集意图为任务链")
    sp.add_argument("--channels", default="youtube",
                    help="逗号分隔：youtube / yt-harvest / x / news / full")
    sp.add_argument("--pipeline", action="store_true", help="采集后自动接 归簇→核查→出卡")
    sp.add_argument("--reset", action="store_true", help="清空旧队列（先备份）")
    sp.add_argument("--yt-query", default="fake news")
    sp.add_argument("--yt-limit", type=int, default=10)
    sp.add_argument("--yt-queries", type=int, default=10)
    sp.add_argument("--mock", action="store_true",
                    help="YouTube 通道用 mock 模式（不调 API，用于演练整条链）")
    sp.add_argument("--x-keyword", default="fake news")
    sp.add_argument("--x-rounds", type=int, default=4)
    sp.add_argument("--x-limit", type=int, default=20)
    sp.add_argument("--news-site", default="all")
    sp.add_argument("--news-limit", type=int, default=15)
    sp.add_argument("--threshold", type=float, default=0.45, help="聚类相似度阈值（与 Agent 默认一致）")
    sp.add_argument("--cards", type=int, default=20, help="出卡数量上限")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("submit", help="手动入队一个任务")
    sp.add_argument("agent", choices=list(ACTION_MANIFEST))
    sp.add_argument("action")
    sp.add_argument("--params", default="{}")
    sp.add_argument("--depends", default="")
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("run", help="执行队列")
    sp.add_argument("--dry", action="store_true", help="只演练，不真跑、不退避、不重试")
    sp.add_argument("--limit", type=int, default=0, help="本次最多执行几个任务")
    sp.add_argument("--max-retries", type=int, default=MAX_RETRIES)
    sp.add_argument("--backoff", default="30,60,120")
    sp.add_argument("--python", default=None, help="指定解释器（默认当前）")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("status", help="查看队列")
    sp.set_defaults(func=cmd_status)

    args = ap.parse_args()
    if args.cmd == "plan" and args.channels == "full":
        args.channels = "youtube,x,news"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
