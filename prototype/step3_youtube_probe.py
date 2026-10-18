"""第 3 步：YouTube 最小试点 —— 只取元数据，不下载媒体（媒体走 yt-dlp，不耗配额）。

两种模式：
  1) mock 模式（无需 API key，验证链路）：
       python step3_youtube_probe.py --mock --query "fake news" --limit 10 --fresh
  2) 真实模式（需 YouTube Data API v3 key；默认日配额 10000 units）：
       python step3_youtube_probe.py --query "fake news" --limit 10
       # key 二选一：--api-key <KEY> 或环境变量 $env:YT_API_KEY（不要写进代码/仓库）

配额成本与账本：
  - search.list = 100 units；videos.list ≈ 1 unit（≤50 个 id/次）；
  - 账本 metadata/quota_ledger.jsonl 每次调用记一行；当日累计 + 本次预估
    超过 --quota-cap（默认 8000，留 2000 余量）时直接拒跑。

代理（本机直连 Google 超时，见 ../documentation/roadmap.md）：
  $env:HTTPS_PROXY='http://127.0.0.1:7897'; $env:HTTP_PROXY='http://127.0.0.1:7897'

输出：metadata/youtube_meta.jsonl（video_id 为主键，含时长/统计/缩略图 url）

媒体下载（下一步、独立跑、零配额）：
  yt-dlp --skip-download --write-info-json --write-thumbnail ^
         --write-auto-subs --sub-langs "zh.*,en.*" ^
         -o "../media/youtube/%(id)s/%(id)s.%(ext)s" https://www.youtube.com/watch?v=<id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
from datetime import date, datetime, timedelta, timezone

from common import METADATA_DIR, append_jsonl, existing_ids, read_user_env_var, setup_logger

log = setup_logger("step3")
OUT = METADATA_DIR / "youtube_meta.jsonl"
LEDGER = METADATA_DIR / "quota_ledger.jsonl"

COST_SEARCH = 100      # search.list
COST_VIDEOS = 1        # videos.list（每次，最多 50 个 id）
SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"

MOCK_TITLES = [
    "SHOCKING video proves {kw}!! (must watch before deleted)",
    "The TRUTH about {kw} that mainstream media hides",
    "Leaked footage of {kw} - SHARE before taken down",
    "{kw} exposed: what they don't want you to see",
    "URGENT warning about {kw} (real footage)",
]


def gen_mock_videos(query: str, limit: int) -> list[dict]:
    """生成确定性 mock 数据，与 step1 风格一致，用于无 key 时先跑通链路。"""
    rng = random.Random(f"yt|{query}|{limit}")
    now = datetime.now(timezone.utc)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    records = []
    for i in range(limit):
        h = hashlib.md5(f"{query}:{i}".encode()).digest()
        vid = "".join(alphabet[b % 64] for b in h)[:11]
        records.append({
            "video_id": vid,
            "title": rng.choice(MOCK_TITLES).format(kw=query) + f" #{i:02d}",
            "description": f"Mock description about {query}. (mock sample)",
            "channel_id": f"UC{h.hex()[:22]}",
            "channel_title": f"channel_{rng.randint(1000, 9999)}",
            "published_at": (now - timedelta(days=rng.uniform(0.5, 60.0))).isoformat(timespec="seconds"),
            "duration_seconds": rng.randint(30, 1800),
            "view_count": rng.randint(100, 2_000_000),
            "like_count": rng.randint(5, 50_000),
            "comment_count": rng.randint(0, 5_000),
            "thumbnail_url": f"mock://thumb/{vid}",
            "tags": ["mock", query.replace(" ", "")],
            "default_language": rng.choice(["en", "en", "zh"]),
            "watch_url": f"https://www.youtube.com/watch?v={vid}",
            "source_query": query,
            "mode": "mock",
            "collected_at": now.isoformat(timespec="seconds"),
        })
    return records


def parse_duration_iso(iso: str) -> int:
    """ISO8601 时长（PT4M13S / PT1H2M3S）转秒。"""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def read_quota_today() -> int:
    """读当日已耗配额（jsonl 每行一次调用）。"""
    today = date.today().isoformat()
    used = 0
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if row.get("date") == today:
                used += int(row.get("units", 0))
    return used


def log_quota(endpoint: str, units: int) -> None:
    append_jsonl(LEDGER, [{
        "date": date.today().isoformat(),
        "endpoint": endpoint,
        "units": units,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }])


def yt_get(endpoint_url: str, params: dict, api_key: str) -> dict:
    import requests
    r = requests.get(endpoint_url, params={**params, "key": api_key}, timeout=30)
    if r.status_code == 403:
        msg = (r.json().get("error", {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else "")
        raise SystemExit(
            f"YouTube API 403：{msg}\n常见原因：1) key 无效/未启用 Data API v3；"
            f"2) 配额用尽（看 {LEDGER.name}）；3) 代理未设置。"
        )
    r.raise_for_status()
    return r.json()


def fetch_live(query: str, limit: int, api_key: str, quota_cap: int) -> list[dict]:
    import requests  # 依赖已随 httpx 生态装好；mock 模式不导入
    planned = COST_SEARCH + (limit + 49) // 50 * COST_VIDEOS
    used = read_quota_today()
    if used + planned > quota_cap:
        raise SystemExit(f"配额保护：今日已用 {used} + 本次预估 {planned} > 上限 {quota_cap}（--quota-cap 可调）。明日再跑或调低 limit。")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # 1) 发现：search.list 只拿 video_id（100 units，一次搞定，绝不翻页循环）
    data = yt_get(SEARCH_ENDPOINT, {
        "part": "id", "type": "video", "q": query,
        "maxResults": min(limit, 50), "relevanceLanguage": "en",
    }, api_key)
    ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
    log_quota("search.list", COST_SEARCH)
    log.info("search.list 命中 %d 个 video_id（+100 units，今日累计 %d）", len(ids), read_quota_today())
    if not ids:
        return []

    # 2) 详情：videos.list 批量（≤50 id/次，1 unit/次）
    records: list[dict] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = yt_get(VIDEOS_ENDPOINT, {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)}, api_key)
        log_quota("videos.list", COST_VIDEOS)
        for it in data.get("items", []):
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            records.append({
                "video_id": it.get("id"),
                "title": sn.get("title"),
                "description": sn.get("description"),
                "channel_id": sn.get("channelId"),
                "channel_title": sn.get("channelTitle"),
                "published_at": sn.get("publishedAt"),
                "duration_seconds": parse_duration_iso(it.get("contentDetails", {}).get("duration")),
                "view_count": int(st.get("viewCount", 0) or 0),
                "like_count": int(st.get("likeCount", 0) or 0),
                "comment_count": int(st.get("commentCount", 0) or 0),
                "thumbnail_url": (thumbs.get("high") or thumbs.get("medium") or {}).get("url"),
                "tags": sn.get("tags") or [],
                "default_language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                "watch_url": f"https://www.youtube.com/watch?v={it.get('id')}",
                "source_query": query,
                "mode": "live",
                "collected_at": now,
            })
    log.info("videos.list 详情 %d 条（+1 unit，今日累计 %d）", len(records), read_quota_today())
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube 最小试点：只取元数据，配额受账本保护")
    ap.add_argument("--query", default="fake news")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--mock", action="store_true", help="无需 API key，生成确定性模拟数据")
    ap.add_argument("--api-key", default=None, help="YouTube Data API v3 key；缺省读环境变量 YT_API_KEY")
    ap.add_argument("--quota-cap", type=int, default=8000, help="当日配额上限（默认 8000，留余量）")
    ap.add_argument("--fresh", action="store_true", help="清空输出文件后重采")
    a = ap.parse_args()

    if a.limit > 50:
        log.warning("limit=%d 超过单次 search 上限 50，试点阶段建议 ≤10。", a.limit)
    if a.fresh and OUT.exists():
        OUT.unlink()
        log.info("已清空 %s", OUT)

    api_key = a.api_key or os.environ.get("YT_API_KEY") or read_user_env_var("YT_API_KEY")
    if not a.mock and not api_key:
        raise SystemExit(
            "缺少 YouTube API key。三选一：\n"
            '  1) 本会话临时： $env:YT_API_KEY=\'你的key\'\n'
            '  2) 永久（推荐，key 不进代码/仓库）： setx YT_API_KEY "你的key"  然后重开终端\n'
            "  3) 命令行传参： --api-key 你的key"
        )

    records = (gen_mock_videos(a.query, a.limit) if a.mock else
               fetch_live(a.query, a.limit, api_key, a.quota_cap))
    if not records:
        log.warning("没有取到任何记录。")
        return

    known = existing_ids(OUT, field="video_id")
    new = [r for r in records if r["video_id"] not in known]
    append_jsonl(OUT, new)
    total = sum(1 for _ in existing_ids(OUT, field="video_id"))
    log.info("新增 %d 条（跳过重复 %d 条），累计 %d 个 video_id -> %s",
             len(new), len(records) - len(new), total, OUT)


if __name__ == "__main__":
    main()
