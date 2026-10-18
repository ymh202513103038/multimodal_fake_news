r"""第 4 步：YouTube 媒体下载 —— 把 youtube_meta.jsonl 里的候选变成多模态本体。

零 API 配额：全部通过 yt-dlp 走视频 CDN，不碰 Data API。

三种 --what（按需选，成本递增）：
  subs   字幕(vtt) + 缩略图 + info.json，不下载视频（默认，最轻）
  audio  仅音频轨（优先 m4a）
  video  ≤720p 视频+音轨合并 mp4（自动检测 venv 内置 ffmpeg；
         没有则退回 360p 预合并格式）+ 缩略图 + info.json

用法：
  python step4_youtube_media.py --what subs --limit 5      # 试点建议从小批量开始
  python step4_youtube_media.py --what video --limit 2
  python step4_youtube_media.py --fresh                    # 清空清单重下

输出：
  media/youtube/{video_id}/          媒体文件本体
  media/youtube/archive.{what}.txt   yt-dlp 幂等清单
  metadata/youtube_media.jsonl       清单：video_id + what + 本地文件相对路径

注意：
  - 需要代理：先 $env:HTTPS_PROXY='http://127.0.0.1:7897'（或 --proxy 传入）；
  - 自动字幕可能被 YouTube 限流（HTTP 429，代理 IP 常见）：失败不影响缩略图/info，
    清单里标 subs_ok=false，下次运行自动重试；字幕的可靠替代方案是 audio 模式 + Whisper 转写；
  - mock 记录自动跳过；
  - video/audio 模式用的是免 ffmpeg 的格式；抽帧需要 ffmpeg（本机暂未安装）。
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import MEDIA_DIR, METADATA_DIR, ROOT, append_jsonl, read_jsonl, setup_logger

log = setup_logger("step4")
IN = METADATA_DIR / "youtube_meta.jsonl"
MANIFEST = METADATA_DIR / "youtube_media.jsonl"
MEDIA_YT = MEDIA_DIR / "youtube"

# 每种模式的基础参数（字幕单独处理：YouTube 对字幕接口限流严重，失败不阻塞）
WHAT_OPTIONS = {
    "subs": ["--skip-download", "--write-thumbnail", "--write-info-json"],
    "audio": ["-f", "ba[ext=m4a]/ba", "--write-thumbnail", "--write-info-json"],
    "video": ["-f", "b[height<=720]/b", "--write-thumbnail", "--write-info-json"],
}
# 有 ffmpeg 时的 video 高清方案（DASH 分离流 + 合并）
VIDEO_FFMPEG_EXTRA = ["-f", "bv*[height<=720]+ba/b", "--merge-output-format", "mp4",
                      "--write-thumbnail", "--write-info-json"]
SUBS_EXTRA = ["--skip-download", "--write-auto-subs",
              "--sub-langs", "en.*,zh.*", "--sub-format", "vtt"]


def find_ffmpeg() -> str | None:
    """venv 内置的 imageio-ffmpeg 优先，其次系统 PATH。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        import shutil
        return shutil.which("ffmpeg")


def load_manifest_state() -> tuple[set, set]:
    """返回 (已完成的 (video_id,what) 集合, 字幕失败待重试的 video_id 集合)。"""
    done, failed_subs = set(), set()
    if MANIFEST.exists():
        for r in read_jsonl(MANIFEST):
            if isinstance(r, dict):
                done.add((r.get("video_id"), r.get("what")))
                if r.get("what") == "subs" and r.get("subs_ok") is False:
                    failed_subs.add(r.get("video_id"))
    return done, failed_subs


def list_video_dir(vid: str) -> list[str]:
    d = MEDIA_YT / vid
    if not d.exists():
        return []
    return sorted(p.relative_to(ROOT).as_posix() for p in d.iterdir() if p.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube 媒体下载（yt-dlp，零 API 配额）")
    ap.add_argument("--what", choices=WHAT_OPTIONS, default="subs")
    ap.add_argument("--limit", type=int, default=5, help="本次最多处理几个视频（试点建议 ≤5）")
    ap.add_argument("--meta", default=str(IN))
    ap.add_argument("--sleep", type=int, default=3, help="视频之间随机休息基数（秒）")
    ap.add_argument("--limit-rate", default="2M", help="限速（礼貌下载）")
    ap.add_argument("--proxy", default=None, help="代理；缺省继承环境变量 HTTPS_PROXY")
    ap.add_argument("--timeout", type=int, default=600, help="单个视频下载超时（秒）")
    ap.add_argument("--fresh", action="store_true", help="清空清单后重下（不删已存在文件）")
    a = ap.parse_args()

    if a.fresh and MANIFEST.exists():
        MANIFEST.unlink()
        log.info("已清空 %s", MANIFEST)

    # 候选：live 记录、未完成的 (video_id, what)；字幕失败过的自动进入重试
    done, failed_subs = load_manifest_state()
    candidates = []
    for r in read_jsonl(Path(a.meta)):
        if r.get("mode") == "mock":
            continue
        vid = r.get("video_id")
        if not vid:
            continue
        already = (vid, a.what) in done
        retry_subs = (a.what == "subs" and vid in failed_subs)
        if not already or retry_subs:
            candidates.append(vid)
    if not candidates:
        log.info("没有待处理视频（%s 模式已全部下载过，或 meta 为空）。", a.what)
        return
    todo = candidates[:a.limit]
    log.info("候选 %d 个，本次处理 %d 个（--what %s）", len(candidates), len(todo), a.what)
    if len(candidates) > len(todo):
        log.info("剩余 %d 个下次继续。", len(candidates) - len(todo))

    # video 模式：有 ffmpeg 就合并 720p 高清，没有则退回预合并（通常 360p）
    video_extra = None
    if a.what == "video":
        ff = find_ffmpeg()
        if ff:
            video_extra = VIDEO_FFMPEG_EXTRA + ["--ffmpeg-location", ff]
            log.info("检测到 ffmpeg：%s", Path(ff).name)
        else:
            log.warning("未找到 ffmpeg，video 模式退回 360p 预合并格式"
                        "（pip install imageio-ffmpeg 可解锁 720p 合并）")

    ok = fail = 0

    def run_ytdlp(vid: str, extra: list[str], timeout: int, use_archive: bool):
        cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings",
               "--retries", "3", "--retry-sleep", "5",
               "--limit-rate", a.limit_rate,
               "-o", str(MEDIA_YT / "%(id)s/%(id)s.%(ext)s")]
        if use_archive:
            cmd += ["--download-archive", str(MEDIA_YT / f"archive.{a.what}.txt")]
        cmd += extra
        if a.proxy:
            cmd += ["--proxy", a.proxy]
        cmd += [f"https://www.youtube.com/watch?v={vid}"]
        return subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)

    for i, vid in enumerate(todo, 1):
        (MEDIA_YT / vid).mkdir(parents=True, exist_ok=True)
        subs_ok = None  # 仅 subs 模式有意义：True/False/None(不适用)

        # 第一阶段：主体（缩略图/info/音频/视频）—— 失败则整条跳过
        extra = WHAT_OPTIONS[a.what]
        if a.what == "video" and video_extra:
            extra = video_extra
        try:
            run_ytdlp(vid, extra, a.timeout, use_archive=True)
        except subprocess.TimeoutExpired:
            fail += 1
            log.error("[%d/%d] %s 主体下载超时（>%ds），跳过", i, len(todo), vid, a.timeout)
            continue
        except subprocess.CalledProcessError as e:
            tail = (e.stderr or b"")[-500:].decode("utf-8", "ignore").strip()
            fail += 1
            log.error("[%d/%d] %s 主体下载失败(code=%d)：%s", i, len(todo), vid,
                      e.returncode, tail or "（常见原因：代理未设/视频不可用/地区限制）")
            continue

        # 第二阶段：字幕（尽力而为；YouTube 对字幕接口限流 429 很常见）
        # 以"目录里是否真的有 .vtt"为准，而非退出码——部分语言失败但 en 成功也算拿到
        if a.what == "subs":
            try:
                run_ytdlp(vid, SUBS_EXTRA, a.timeout, use_archive=False)
            except subprocess.CalledProcessError as e:
                tail = (e.stderr or b"")[-300:].decode("utf-8", "ignore").strip()
                log.warning("[%d/%d] %s 部分字幕语言失败（如限流 429）：%s",
                            i, len(todo), vid, tail.splitlines()[-1] if tail else "无详情")
            except subprocess.TimeoutExpired:
                log.warning("[%d/%d] %s 字幕下载超时", i, len(todo), vid)
            subs_ok = any(p.suffix == ".vtt" for p in (MEDIA_YT / vid).glob("*.vtt"))
            if not subs_ok:
                log.warning("[%d/%d] %s 字幕仍缺失，已计入下次自动重试", i, len(todo), vid)

        files = list_video_dir(vid)
        rec = {
            "video_id": vid, "what": a.what,
            "media_local": files,
            "file_count": len(files),
            "subs_ok": subs_ok,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # upsert：替换同 (video_id, what) 的旧条目，重试不会让清单膨胀
        rows = [r for r in read_jsonl(MANIFEST)] if MANIFEST.exists() else []
        rows = [r for r in rows
                if not (r.get("video_id") == vid and r.get("what") == a.what)]
        rows.append(rec)
        MANIFEST.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8")
        ok += 1
        note = "，字幕缺失" if subs_ok is False else ""
        log.info("[%d/%d] %s -> %d 个文件%s", i, len(todo), vid, len(files), note)

        if i < len(todo):
            time.sleep(random.uniform(2, a.sleep + 2))  # 礼貌间隔

    log.info("完成：%d 成功，%d 失败 -> %s", ok, fail, MANIFEST)
    if fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
