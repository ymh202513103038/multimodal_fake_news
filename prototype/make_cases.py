r"""把原始采集层（metadata + media）组装成"案例制"数据集层（参考 ex 样例结构）。

原始层（平台/ID 制，采集友好，每天自动增长）：
  metadata/youtube_meta.jsonl + media/youtube/{video_id}/*
案例层（事件制，研究/发布友好，本脚本生成）：
  dataset/index.json                       总索引（ex.json 风格，入库）
  dataset/yt_{video_id}/                   一个案例一个文件夹
    ├── image/    缩略图 png（+日后关键帧、证据图）
    ├── video/    mp4（硬链接，不占双份空间）
    ├── wav/      16kHz 单声道 wav（ASR/声学特征友好）
    ├── text/     text.txt（标题+简介+字幕纯文本）、subs.vtt
    ├── reason/   reason.txt（标注理由；未标注为占位）
    └── web/      source.txt（来源 URL + 证据链接；二期换网页快照）

标签/理由来自 metadata/label_sheet.csv（youtube:{video_id} 行）。
用法：
  python make_cases.py                                # 全部 live 视频建案
  python make_cases.py --vids gdwzqEosLtU,zCZZkBsIhso # 指定视频
  python make_cases.py --fresh                        # 清空 dataset/ 重建
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import DATASET_DIR, MEDIA_DIR, METADATA_DIR, ROOT, read_jsonl, setup_logger
from step4_youtube_media import find_ffmpeg

log = setup_logger("make_cases")
MEDIA_YT = MEDIA_DIR / "youtube"
LABEL_SHEET = METADATA_DIR / "label_sheet.csv"
SUBDIRS = ["image", "video", "wav", "text", "reason", "web"]
LABEL_MAP = {"1": "fake", "0": "real", "-1": "unverified"}


def load_labels() -> dict:
    """label_sheet.csv -> {video_id: {label, modality_type, evidence_url, rationale, annotator}}"""
    out = {}
    if not LABEL_SHEET.exists():
        return out
    with open(LABEL_SHEET, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sid = row.get("sample_id") or ""
            if not sid.startswith("youtube:"):
                continue
            vid = sid.split(":", 1)[1]
            if row.get("is_fake"):
                out[vid] = row
    return out


def vtt_to_text(vtt_path: Path) -> str:
    """极简 vtt 清理：去头/时间轴/样式行与内联标签，去重连续行，得纯文本。"""
    import re
    lines, seen = [], set()
    for raw in vtt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if (not line or line == "WEBVTT" or "-->" in line
                or line.isdigit() or line.startswith(("NOTE", "STYLE", "Kind:", "Language:"))):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


def link_or_copy(src: Path, dst: Path) -> None:
    """同盘优先硬链接（不占双份空间），失败再复制。"""
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def ffmpeg_run(args: list[str]) -> bool:
    ff = find_ffmpeg()
    if not ff:
        return False
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", *args],
                       check=True, timeout=300)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ffmpeg 失败（%s）：%s", args[:2], e)
        return False


def build_case(meta: dict, media: dict, label: dict | None, ff: str | None) -> dict:
    vid = meta["video_id"]
    case_dir = DATASET_DIR / f"yt_{vid}"
    for sub in SUBDIRS:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    media_files = {Path(p).name: ROOT / p for p in media.get("media_local", [])}
    files_out: dict[str, list[str]] = {k: [] for k in SUBDIRS}

    # video/
    mp4 = media_files.get(f"{vid}.mp4")
    if mp4 and mp4.exists():
        dst = case_dir / "video" / f"{vid}.mp4"
        link_or_copy(mp4, dst)
        files_out["video"].append(str(dst.relative_to(DATASET_DIR)).replace("\\", "/"))
        # wav/：16kHz 单声道，ASR 友好
        wav_dst = case_dir / "wav" / f"{vid}.wav"
        if not wav_dst.exists() and ff:
            ffmpeg_run(["-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav_dst)])
        if wav_dst.exists():
            files_out["wav"].append(str(wav_dst.relative_to(DATASET_DIR)).replace("\\", "/"))

    # audio 模式下载的 m4a（没有 mp4 时也能出 wav）
    m4a = media_files.get(f"{vid}.m4a")
    if m4a and m4a.exists() and not files_out["wav"] and ff:
        wav_dst = case_dir / "wav" / f"{vid}.wav"
        ffmpeg_run(["-i", str(m4a), "-vn", "-ac", "1", "-ar", "16000", str(wav_dst)])
        if wav_dst.exists():
            files_out["wav"].append(str(wav_dst.relative_to(DATASET_DIR)).replace("\\", "/"))

    # image/：缩略图 webp -> png（转换失败则原样复制）
    thumb = media_files.get(f"{vid}.webp")
    if thumb and thumb.exists():
        png_dst = case_dir / "image" / f"{vid}.png"
        ok = ff and ffmpeg_run(["-i", str(thumb), str(png_dst)])
        if not (ok and png_dst.exists()):
            link_or_copy(thumb, case_dir / "image" / f"{vid}.webp")
            files_out["image"].append(f"yt_{vid}/image/{vid}.webp")
        else:
            files_out["image"].append(f"yt_{vid}/image/{vid}.png")

    # text/：标题+简介 + 字幕
    text_parts = [f"# {meta.get('title', '')}", "", meta.get("description") or ""]
    subs_src = media_files.get(f"{vid}.en.vtt") or media_files.get(f"{vid}.en-orig.vtt")
    if subs_src and subs_src.exists():
        shutil.copy2(subs_src, case_dir / "text" / "subs.vtt")
        files_out["text"].append(f"yt_{vid}/text/subs.vtt")
        text_parts += ["", "## 字幕（en，自动）", vtt_to_text(subs_src)]
    text_dst = case_dir / "text" / "text.txt"
    text_dst.write_text("\n".join(text_parts).strip() + "\n", encoding="utf-8")
    files_out["text"].insert(0, f"yt_{vid}/text/text.txt")

    # reason/ + label（来自标注表）
    rationale = (label or {}).get("rationale", "").strip()
    if not rationale:
        rationale = "（待标注）"
    (case_dir / "reason" / "reason.txt").write_text(rationale + "\n", encoding="utf-8")
    files_out["reason"].append(f"yt_{vid}/reason/reason.txt")

    # web/：来源与证据（现阶段记 URL，二期换网页快照）
    ev = (label or {}).get("evidence_url", "").strip()
    (case_dir / "web" / "source.txt").write_text(
        f"source_url: {meta.get('watch_url', '')}\n"
        f"evidence_url: {ev or '（待核查对齐）'}\n", encoding="utf-8")
    files_out["web"].append(f"yt_{vid}/web/source.txt")

    is_fake = (label or {}).get("is_fake")
    return {
        "id": f"yt_{vid}",
        "event": None,
        "topic": None,
        "platform": "youtube",
        "url": meta.get("watch_url"),
        "video_id": vid,
        "label": LABEL_MAP.get(is_fake) if is_fake else None,
        "modality_type": (label or {}).get("modality_type") or None,
        "reason": rationale if rationale != "（待标注）" else None,
        "annotator": (label or {}).get("annotator") or None,
        "title": meta.get("title"),
        "metrics": {"views": meta.get("view_count"), "likes": meta.get("like_count"),
                    "comments": meta.get("comment_count")},
        "collected_at": meta.get("collected_at"),
        "case_dir": f"yt_{vid}",
        "files": {k: v for k, v in files_out.items() if v},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="组装案例制数据集（ex 风格）")
    ap.add_argument("--vids", default=None, help="逗号分隔的 video_id；缺省全部 live")
    ap.add_argument("--fresh", action="store_true", help="清空 dataset/ 重建")
    a = ap.parse_args()

    if a.fresh and DATASET_DIR.exists():
        shutil.rmtree(DATASET_DIR)
        log.info("已清空 %s", DATASET_DIR)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    ff = find_ffmpeg()
    labels = load_labels()
    media_map = {}
    manifest = METADATA_DIR / "youtube_media.jsonl"
    if manifest.exists():
        for r in read_jsonl(manifest):
            if isinstance(r, dict):
                media_map[r.get("video_id")] = r

    targets = a.vids.split(",") if a.vids else None
    cases, skipped = [], 0
    for meta in read_jsonl(METADATA_DIR / "youtube_meta.jsonl"):
        if meta.get("mode") == "mock":
            continue
        vid = meta.get("video_id")
        if not vid or (targets and vid not in targets):
            continue
        if not media_map.get(vid):
            skipped += 1
            continue  # 没下过任何媒体的暂不建案
        cases.append(build_case(meta, media_map[vid], labels.get(vid), ff))

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(cases),
        "schema": "ex-style case index: id/event/topic/label/reason + modality files",
        "cases": cases,
    }
    (DATASET_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("建案 %d 个（跳过未下载媒体 %d 个）-> %s",
             len(cases), skipped, DATASET_DIR / "index.json")


if __name__ == "__main__":
    main()
