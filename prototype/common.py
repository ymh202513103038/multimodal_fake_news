"""共享工具：路径、日志、jsonl 读写。仅用标准库，保证 mock 模式零依赖可跑。"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METADATA_DIR = ROOT / "metadata"
MEDIA_DIR = ROOT / "media"
LOGS_DIR = ROOT / "logs"
DOCS_DIR = ROOT / "docs"

for _d in (METADATA_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(LOGS_DIR / "pipeline.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


def append_jsonl(path: Path, records) -> int:
    n = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def existing_ids(path: Path, field: str = "tweet_id") -> set:
    """读 jsonl 里已有的主键集合（X 用 tweet_id，YouTube 用 video_id）。"""
    return {r.get(field) for r in read_jsonl(path) if isinstance(r, dict)}
