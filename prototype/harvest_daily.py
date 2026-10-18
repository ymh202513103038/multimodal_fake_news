r"""每日收割器：用免费配额轮转关键词，持续扩大 metadata/youtube_meta.jsonl。

设计：
  - 每天（计划任务或手动）运行一次；从 youtube_keywords.txt 按 cursor 轮转取 N 个词；
  - 每个词成本 ≈ 101 units（search 100 + details 1），默认 10 词/天 ≈ 1010 units，
    远低于 10000 免费额度；quota_cap 双保险，接近上限自动停；
  - youtube_meta.jsonl 按 video_id 幂等去重，重复词不会产生重复数据；
  - 断点：cursor 存 metadata/harvest_state.json，每天接着上次的位置转。

用法：
  python harvest_daily.py                 # 正式收割（默认 10 词 x 25 条）
  python harvest_daily.py --queries 3     # 少采一点
  python harvest_daily.py --dry-run       # 只看计划不调 API（0 配额）
  python harvest_daily.py --keywords prototype\youtube_keywords.txt

key 来源：环境变量 YT_API_KEY（setx 持久化）> 用户级注册表 > --api-key。
计划任务入口见 run_harvest.bat；手动跑一次也可以直接执行本文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from common import METADATA_DIR, ROOT, append_jsonl, existing_ids, read_user_env_var, setup_logger
from step3_youtube_probe import OUT, fetch_live, read_quota_today

log = setup_logger("harvest")
KEYWORDS_FILE = ROOT / "prototype" / "youtube_keywords.txt"
STATE = METADATA_DIR / "harvest_state.json"
COST_PER_QUERY = 101  # search.list 100 + videos.list 1（limit<=50 时）


def load_keywords(path) -> list[str]:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"关键词文件不存在：{path}")
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            words.append(w)
    if not words:
        raise SystemExit("关键词文件是空的：每行写一个检索词（# 为注释）。")
    return words


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("状态文件损坏，重新开始轮转。")
    return {"cursor": 0, "history": []}


def get_api_key() -> str:
    key = os.environ.get("YT_API_KEY") or read_user_env_var("YT_API_KEY")
    if not key:
        raise SystemExit(
            "缺少 YT_API_KEY。在你的终端执行一次（key 不会进代码/仓库）：\n"
            '  setx YT_API_KEY "你的key"\n'
            "然后重开终端，或直接把 key 写进计划任务环境。"
        )
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube 每日配额收割器")
    ap.add_argument("--keywords", default=str(KEYWORDS_FILE))
    ap.add_argument("--queries", type=int, default=10, help="本次采集的关键词个数（默认 10 ≈ 1010 units）")
    ap.add_argument("--limit", type=int, default=25, help="每个关键词最多取多少条（≤50）")
    ap.add_argument("--quota-cap", type=int, default=8000)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不调 API")
    a = ap.parse_args()

    keywords = load_keywords(a.keywords)
    state = load_state()
    cursor = state.get("cursor", 0) % len(keywords)
    end = min(cursor + a.queries, len(keywords))
    picked = keywords[cursor:end]
    next_cursor = end if end < len(keywords) else 0

    used = read_quota_today()
    log.info("今日已用 %d units；本次计划 %d 个词（每个 ≈ %d units，limit=%d）",
             used, len(picked), COST_PER_QUERY, a.limit)
    log.info("本次词表：%s", " | ".join(picked))
    if a.dry_run:
        log.info("dry-run 结束，未消耗配额。下次轮转位置 cursor=%d", next_cursor)
        return

    key = get_api_key()
    known = existing_ids(OUT, field="video_id")
    done, new_total, stop = 0, 0, None
    for kw in picked:
        if used + COST_PER_QUERY > a.quota_cap:
            stop = f"配额保护触发（已用 {used} ≥ 上限 {a.quota_cap}），剩余词明天继续"
            break
        try:
            records = fetch_live(kw, a.limit, key, a.quota_cap)
        except SystemExit as e:  # step3 的配额/key 防护
            stop = str(e)
            break
        used = read_quota_today()
        fresh = [r for r in records if r["video_id"] not in known]
        append_jsonl(OUT, fresh)
        known.update(r["video_id"] for r in fresh)
        new_total += len(fresh)
        done += 1
        log.info("[%d/%d] %r -> 取 %d 条，新增 %d（累计 %d video_id，今日已用 %d units）",
                 done, len(picked), kw, len(records), len(fresh), len(known), used)

    state.update({
        "cursor": (cursor + done) % len(keywords) if stop is None else next_cursor,
        "history": (state.get("history") or [])[-29:] + [{
            "date": date.today().isoformat(),
            "queries": done,
            "new_videos": new_total,
            "quota_used": used,
            "stopped": stop,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }],
    })
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("今日完成 %d 个词，新增 %d 条 -> %s", done, new_total, OUT)
    if stop:
        log.info("提前停止：%s", stop)
    log.info("累计 %d 个 video_id；下次轮转位置 cursor=%d（词表第 %d 个）",
             len(known), state["cursor"], state["cursor"] + 1)


if __name__ == "__main__":
    main()
