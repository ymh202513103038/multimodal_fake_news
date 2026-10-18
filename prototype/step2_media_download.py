"""第 2 步：媒体下载 + 元数据关联 + 去重。

处理 ../metadata/twitter_raw.jsonl，把图片/视频落到 ../media/twitter/，
生成 ../metadata/twitter_meta.jsonl（元数据 + 本地媒体相对路径）。

用法：
  python step2_media_download.py                 # mock 数据自动识别
  python step2_media_download.py --use-ytdlp     # 真实视频用 yt-dlp 按帖子 URL 下载
  python step2_media_download.py --fresh         # 重跑前清空输出

mock:// 链接会生成本地占位媒体（合法 PNG / 带 ftyp 头的 mp4 占位）；
真实 http(s) 图片用 requests；视频走 yt-dlp（X 视频是 m3u8 变体，直接存 mp4 不可播）。
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

from common import LOGS_DIR, MEDIA_DIR, METADATA_DIR, ROOT, append_jsonl, existing_ids, read_jsonl, setup_logger

log = setup_logger("step2")
IN = METADATA_DIR / "twitter_raw.jsonl"
OUT = METADATA_DIR / "twitter_meta.jsonl"
ERRORS = LOGS_DIR / "download_errors.jsonl"


def make_png(width: int = 64, height: int = 64, rgb: tuple = (200, 30, 30)) -> bytes:
    """手工构造一个合法的小 PNG（无需 PIL）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def download_mock(url: str, dest: Path) -> int:
    h = hashlib.md5(url.encode()).digest()
    if url.startswith("mock://img/"):
        dest.write_bytes(make_png(64, 64, (h[0], h[1], h[2])))
    else:
        ftyp = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        dest.write_bytes(ftyp + b"\x00" * 512)
    return dest.stat().st_size


def download_photo(url: str, dest: Path, max_bytes: int = 50 * 1024 * 1024) -> int:
    import requests  # 仅真实模式需要
    r = requests.get(url, timeout=30, headers={"User-Agent": "academic-research-probe/0.1"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if ctype and not ctype.startswith("image/"):
        raise ValueError(f"非图片 content-type: {ctype}")
    data = r.content
    if len(data) > max_bytes:
        raise ValueError("文件过大")
    dest.write_bytes(data)
    return len(data)


def download_video_ytdlp(post_url: str, base: Path) -> list[Path]:
    """用 yt-dlp 下载视频（推荐：X 视频含 m3u8 变体，requests 拿不到可播放文件）。"""
    import subprocess
    cmd = ["yt-dlp", "--no-playlist", "-o", f"{base}.%(ext)s", post_url]
    subprocess.run(cmd, check=True, timeout=600, capture_output=True)
    return sorted(base.parent.glob(base.name + ".*"))


def main() -> None:
    ap = argparse.ArgumentParser(description="媒体下载与元数据关联")
    ap.add_argument("--input", default=str(IN))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--media-dir", default=str(MEDIA_DIR / "twitter"))
    ap.add_argument("--use-ytdlp", action="store_true", help="真实视频用 yt-dlp 按帖子 URL 下载")
    ap.add_argument("--fresh", action="store_true", help="清空输出 jsonl 后重跑")
    a = ap.parse_args()

    in_path, out_path = Path(a.input), Path(a.out)
    media_dir = Path(a.media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    if a.fresh and out_path.exists():
        out_path.unlink()

    known = existing_ids(out_path)
    ok_files = fail_files = skipped = 0
    total_bytes = 0

    for rec in read_jsonl(in_path):
        tid = rec.get("tweet_id")
        if not tid or tid in known:
            skipped += 1
            continue
        local: list[str] = []
        failed: list[dict] = []
        for i, m in enumerate(rec.get("media") or []):
            url = m.get("url") or ""
            try:
                if m.get("type") == "photo":
                    dest = media_dir / f"{tid}_{i:02d}.{m.get('ext', 'jpg')}"
                    if url.startswith("mock://"):
                        size = download_mock(url, dest)
                    elif url.startswith(("http://", "https://")):
                        size = download_photo(url, dest)
                    else:
                        raise ValueError(f"不支持的媒体 URL: {url[:80]}")
                    local.append(dest.relative_to(ROOT).as_posix())
                else:  # video / gif
                    if url.startswith("mock://"):
                        dest = media_dir / f"{tid}_{i:02d}.{m.get('ext', 'mp4')}"
                        size = download_mock(url, dest)
                        local.append(dest.relative_to(ROOT).as_posix())
                    elif a.use_ytdlp:
                        base = media_dir / f"{tid}_{i:02d}"
                        products = download_video_ytdlp(rec.get("post_url") or url, base)
                        if not products:
                            raise ValueError("yt-dlp 未产出文件")
                        for p in products:
                            local.append(p.relative_to(ROOT).as_posix())
                        size = sum(p.stat().st_size for p in products)
                    else:
                        raise ValueError("视频需加 --use-ytdlp（直连是 m3u8 变体，不能当 mp4 存）")
                ok_files += 1
                total_bytes += size
                log.info("下载完成 %s (%d B)", local[-1], size)
            except Exception as e:  # noqa: BLE001
                fail_files += 1
                failed.append({"tweet_id": tid, "url": url, "error": str(e)})
                log.error("下载失败 tweet=%s: %s", tid, e)

        meta = dict(rec)
        meta["media_local"] = local
        meta["media_failed_count"] = len(failed)
        meta["downloaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        append_jsonl(out_path, [meta])
        known.add(tid)
        if failed:
            append_jsonl(ERRORS, failed)

    log.info("完成：成功媒体 %d，失败 %d，跳过已存在推文 %d，总字节 %.1f KB -> %s",
             ok_files, fail_files, skipped, total_bytes / 1024, out_path)


if __name__ == "__main__":
    main()
