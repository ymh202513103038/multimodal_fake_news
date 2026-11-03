# -*- coding: utf-8 -*-
"""Agent 进程入口：一个任务信封进，一个结果信封出。

两种调用形态：
  1. 契约形态（主控用）：`python agents.py --task-file logs/.task_xxx.json`
  2. 手敲形态（人调试用）：`python agents.py cluster cluster_cases --params "{\"threshold\":0.3}"`
     或 getattr 风格简写：`python agents.py card make_card --event_id E01`

收尾一律在 stdout 最后一行打 `@@AGENT_RESULT@@{json}`；
无论成功失败都打——主控靠这一行裁决，不靠退出码猜。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone

from agent_protocol import (ACTION_MANIFEST, emit, make_result, make_task,
                            now_iso)

MODULES = {
    "collect": "agent_collect",
    "cluster": "agent_cluster",
    "verify": "agent_verify",
    "card": "agent_card",
}


def _load_module(agent: str):
    mod_name = MODULES[agent]
    return __import__(mod_name)


def execute(task: dict) -> dict:
    t0 = time.time()
    agent = task["agent"]
    action = task["action"]
    params = dict(task.get("params") or {})
    try:
        mod = _load_module(agent)
        partial = mod.run(action, params) if agent == "collect" else mod.run(params)
    except Exception as e:  # noqa: BLE001 —— Agent 内部任何崩溃都要变成结果信封
        return make_result(task, "error",
                           error=f"{type(e).__name__}: {e}",
                           metrics={"traceback_tail": traceback.format_exc()[-800:]},
                           elapsed_s=time.time() - t0)

    status = partial.pop("status", "ok") if isinstance(partial, dict) else "error"
    if not isinstance(partial, dict):
        partial = {"outputs": {}, "error": "Agent 返回值不是 dict"}
        status = "error"
    return make_result(task, status,
                       outputs=partial.get("outputs"),
                       artifacts=partial.get("artifacts"),
                       metrics=partial.get("metrics"),
                       error=partial.get("error"),
                       next_hint=partial.get("next"),
                       elapsed_s=time.time() - t0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 进程入口")
    ap.add_argument("agent", nargs="?", choices=list(ACTION_MANIFEST))
    ap.add_argument("action", nargs="?")
    ap.add_argument("--task-file", help="任务信封 JSON 路径（主控用）")
    ap.add_argument("--params", default=None, help="参数 JSON 字符串（手敲用）")
    ap.add_argument("--timeout-s", type=int, default=900)
    args = ap.parse_args()

    if args.task_file:
        import pathlib
        task = json.loads(pathlib.Path(args.task_file).read_text(encoding="utf-8"))
    else:
        if not (args.agent and args.action):
            ap.error("需要 --task-file，或同时给 agent + action")
        params = json.loads(args.params) if args.params else {}
        task = make_task(args.agent, args.action, params, timeout_s=args.timeout_s)

    task.setdefault("_started_at", now_iso())
    task["_started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = execute(task)
    emit(result)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
