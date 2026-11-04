# -*- coding: utf-8 -*-
"""进程内批量出卡 + 校验（r11 教训：子进程方式偶发"调用失败/静默不写"）。

与 `agents.py card make_card` 的区别：
  - **直接 import agent_card 在本进程内执行**，不经子进程——绕开 Windows 下
    杀软扫描导致的偶发 "Unable to create process"（r5/r11/r12 三次踩坑）；
  - 出卡后自动做两道校验：①mtime 变新（复用 agent_card 的 stale_rewrites）；
    ②文件存在且非空；`--require-grounded` 时还要求卡里出现「来源称：」；
  - 任一校验失败以非零码退出——**适合当提交的前置门槛**（r11 教训：校验必须
    在 commit 之前）。

用法：
  python prototype/refresh_cards.py --events E01,E13,E31
  python prototype/refresh_cards.py --events E01,E13 --require-grounded
  python prototype/refresh_cards.py --all --limit 96
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_card import PACKETS, REPORTS, run as card_run
from common import ROOT


def _validate(ids: list[str], require_grounded: bool) -> list[str]:
    problems: list[str] = []
    for eid in ids:
        f = REPORTS / f"card_{eid}.md"
        if not f.exists() or f.stat().st_size == 0:
            problems.append(f"{eid}: 卡片缺失或空文件")
            continue
        if require_grounded:
            text = f.read_text(encoding="utf-8", errors="replace")
            if "来源称：" not in text:
                problems.append(f"{eid}: 卡内无「来源称：」判定（要求 grounded）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--events", help="逗号分隔的事件 ID 列表")
    g.add_argument("--all", action="store_true", help="全部事件（配合 --limit）")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--require-grounded", action="store_true",
                    help="校验卡内必须出现「来源称：」（适合刚回填完的事件）")
    ap.add_argument("--packets", help="覆盖 packets 路径（默认 metadata/verify_packets.json）")
    a = ap.parse_args()

    params: dict = {"limit": a.limit}
    if a.events:
        params["event_id"] = "all"  # 走 all+targets 过滤的等价路径
        ids = [s.strip() for s in a.events.split(",") if s.strip()]
    else:
        ids = []

    if a.packets:
        params["packets"] = a.packets

    # --events 模式：逐个调用，确保精确集合（all+limit 会重排全量）
    results = []
    if ids:
        for eid in ids:
            p = dict(params)
            p["event_id"] = eid
            r = card_run(p)
            results.append((eid, r))
    else:
        r = card_run(params)
        ev_data = r  # 占位：all 模式下 ids 在 card_run 内部展开，改为读输出文件
        import json
        evs = json.loads((ROOT / "metadata" / "events.json").read_text(encoding="utf-8"))
        ids = [e["event_id"] for e in evs.get("events", [])][: a.limit]
        results.append(("ALL", r))

    # 汇总校验
    all_ids = [eid for eid, _ in results if eid != "ALL"] or ids
    problems = _validate(all_ids, a.require_grounded)
    for eid, r in results:
        if r.get("status") != "ok":
            problems.append(f"{eid}: card_run status={r.get('status')} error={r.get('error')}")
        else:
            st = (r.get("outputs") or {}).get("stale_rewrites") or []
            problems.extend(f"{eid}: stale rewrite {s}" for s in st)

    if problems:
        print("FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print(f"OK：{len(all_ids)} 张卡全部通过校验"
          + ("（含「来源称：」检查）" if a.require_grounded else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
