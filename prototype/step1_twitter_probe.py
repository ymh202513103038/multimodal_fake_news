"""第 1 步：X(Twitter) 最小试点 —— 只取多模态元数据，不下载媒体。

两种模式：
  1) mock 模式（无需账号，立即可跑，验证链路）：
       python step1_twitter_probe.py --mock --keyword "fake news" --limit 20 --fresh
  2) 真实模式（需先用 twscrape 添加账号，见 ../documentation/roadmap.md）：
       python step1_twitter_probe.py --keyword "fake news" --limit 20

输出：../metadata/twitter_raw.jsonl（含 tweet_id / 正文 / 图片视频 url）
约束：默认小批量；limit>50 会告警，试点阶段建议 20。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
from datetime import date, datetime, timedelta, timezone

from common import METADATA_DIR, append_jsonl, existing_ids, setup_logger

log = setup_logger("step1")
OUT = METADATA_DIR / "twitter_raw.jsonl"

MOCK_TEXTS = [
    "URGENT: {kw} spreading RIGHT NOW! Officials are hiding this!!! (mock sample)",
    "SHOCKING photo proves {kw} - media won't show you. RT before deleted! (mock sample)",
    "Leaked video about {kw}. Watch before it's taken down. Thread. (mock sample)",
    "Anonymous insider confirms {kw}. The truth they don't want you to see. (mock sample)",
    "1 like = 1 prayer. {kw} exposed in these photos. (mock sample)",
]


def gen_mock_tweets(keyword: str, limit: int) -> list[dict]:
    """生成确定性 mock 数据，用于在没有 X 账号时先验证整条链路。"""
    rng = random.Random(f"{keyword}|{limit}")
    base_id = 1_700_000_000_000_000_000
    now = datetime.now(timezone.utc)
    records = []
    for i in range(limit):
        tid = str(base_id + i)
        text = rng.choice(MOCK_TEXTS).format(kw=keyword) + f" #{i:02d}"
        media = []
        if rng.random() < 0.75:
            for j in range(rng.randint(1, 3)):
                h = hashlib.md5(f"{tid}:{j}".encode()).hexdigest()[:12]
                media.append({"type": "photo", "url": f"mock://img/{h}", "ext": "png"})
        else:
            h = hashlib.md5(f"{tid}:v".encode()).hexdigest()[:12]
            media.append({"type": "video", "url": f"mock://video/{h}", "ext": "mp4"})
        created = now - timedelta(days=rng.uniform(0.1, 30.0))
        records.append({
            "tweet_id": tid,
            "created_at": created.isoformat(timespec="seconds"),
            "text": text,
            "lang": "en",
            "user": {"id": 900000 + i, "username": f"user_{rng.randint(1000, 9999)}"},
            "post_url": f"https://x.com/mock_user/status/{tid}",
            "media": media,
            "source_query": f"{keyword} (filter:photos OR filter:videos) [MOCK]",
            "mode": "mock",
            "collected_at": now.isoformat(timespec="seconds"),
        })
    return records


def extract_media(tweet) -> list[dict]:
    """把 twscrape Tweet 对象里的媒体规范化为 [{type, url, ext}]。"""
    out = []
    for m in getattr(tweet, "media", None) or []:
        kind = type(m).__name__
        if kind == "Photo":
            url = getattr(m, "url", None)
            if not url:
                continue
            ext = (url.rsplit("?", 1)[0].rsplit(".", 1)[-1] or "jpg").lower()
            if ext not in {"jpg", "jpeg", "png", "webp"}:
                ext = "jpg"
            out.append({"type": "photo", "url": url, "ext": ext})
        elif kind in {"Video", "Gif"}:
            variants = sorted(
                getattr(m, "variants", []) or [],
                key=lambda v: getattr(v, "bitrate", 0) or 0,
                reverse=True,
            )
            if variants:
                out.append({
                    "type": "video" if kind == "Video" else "gif",
                    "url": variants[0].url,
                    "ext": "mp4",
                })
    return out


async def fetch_live(keyword: str, limit: int, since: str, until: str) -> list[dict]:
    try:
        import twscrape
    except ImportError as e:
        raise SystemExit(
            "未安装 twscrape。先激活虚拟环境并安装：\n"
            "  fake_news_env\\Scripts\\activate\n"
            "  pip install twscrape"
        ) from e

    api = twscrape.API()  # 默认读写当前目录的 accounts.sqlite
    try:
        accounts = await api.pool.accounts_info()
        log.info("账号池：%d 个账号", len(accounts))
        if not accounts:
            raise SystemExit(
                "账号池为空。先执行：\n"
                "  twscrape add_account <用户名> <密码> <邮箱>:<邮箱密码>\n"
                "  twscrape accounts"
            )
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("读取账号池失败（忽略并继续）：%s", e)

    query = f"{keyword} (filter:photos OR filter:videos) since:{since} until:{until}"
    log.info("检索：%s | limit=%d", query, limit)
    records: list[dict] = []
    errors = 0
    try:
        async for tw in api.search(query, limit=limit):
            try:
                user = getattr(tw, "user", None)
                username = getattr(user, "username", "unknown")
                records.append({
                    "tweet_id": str(tw.id),
                    "created_at": tw.date.isoformat() if getattr(tw, "date", None) else None,
                    "text": getattr(tw, "rawContent", None) or getattr(tw, "text", "") or "",
                    "lang": getattr(tw, "lang", None),
                    "user": {"id": getattr(user, "id", None), "username": username},
                    "post_url": f"https://x.com/{username}/status/{tw.id}",
                    "media": extract_media(tw),
                    "source_query": query,
                    "mode": "live",
                    "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.warning("单条解析失败：%s", e)
    except Exception as e:  # noqa: BLE001
        log.error("twscrape 拉取失败：%s", e)
        log.error("常见原因：1) 未添加账号；2) 触发限流(429)/封号；3) 网络无法访问 X。")
        raise SystemExit(1) from e

    log.info("实采 %d 条，解析失败 %d 条", len(records), errors)
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="X 最小试点：只取元数据")
    ap.add_argument("--keyword", default="fake news")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--mock", action="store_true", help="无需账号，生成确定性模拟数据")
    ap.add_argument("--fresh", action="store_true", help="清空输出文件后重采")
    ap.add_argument("--since", default=(date.today() - timedelta(days=30)).isoformat())
    ap.add_argument("--until", default=(date.today() + timedelta(days=1)).isoformat())
    a = ap.parse_args()

    if a.limit > 50:
        log.warning("limit=%d 超过试点建议上限 50；大流量容易触发限流/封号。", a.limit)
    if a.fresh and OUT.exists():
        OUT.unlink()
        log.info("已清空 %s", OUT)

    known = existing_ids(OUT)
    records = (gen_mock_tweets(a.keyword, a.limit) if a.mock
               else asyncio.run(fetch_live(a.keyword, a.limit, a.since, a.until)))
    new = [r for r in records if r["tweet_id"] not in known]
    skipped = len(records) - len(new)
    n = append_jsonl(OUT, new)
    media_total = sum(len(r.get("media", [])) for r in new)
    no_media = sum(1 for r in new if not r.get("media"))
    log.info("新增 %d 条（跳过重复 %d 条），媒体 url %d 个，无媒体 %d 条 -> %s",
             n, skipped, media_total, no_media, OUT)


if __name__ == "__main__":
    main()
