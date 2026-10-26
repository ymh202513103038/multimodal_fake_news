# -*- coding: utf-8 -*-
"""全状态 JSON 接口（只读）——为调度 Agent / 上层编排预留的状态查询层。

设计原则（为什么不是一个巨型端点）：
  1. /api/state 给"一眼总览"（Agent 决策用小载荷），资源明细走子端点分页拉取，
     避免一次性把 97 案例+媒体清单塞爆 Agent 上下文窗口；
  2. 所有响应带 schema_version + generated_at，Agent 可做新鲜度判断；
  3. 严格只读：任何写操作仍走 prototype 里各 CLI 脚本，接口不提供写通道；
  4. 默认只绑 127.0.0.1，数据含采集原文，勿直接暴露公网（要暴露请自行加鉴权反代）。

端点：
  GET /api/health                 存活探针
  GET /api/state                  全状态总览（案例统计/标签覆盖/配额/收割游标/媒体体量）
  GET /api/cases                  案例列表，支持过滤与分页
                                  ?platform=youtube|twitter|piyao|snopes
                                  &topic=&lang=&label=true|false|null&role=
                                  &thin=1(正文<120字) &q=关键词 &limit=&offset=
  GET /api/cases/{id}             单案例详情（含 files 清单与实际磁盘文件）
  GET /api/harvest                收割游标 + 每日配额账本聚合

用法（仓库根目录）：
  python prototype/state_api.py                  # 127.0.0.1:8765
  python prototype/state_api.py --port 8800
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "dataset"
META = ROOT / "metadata"
SCHEMA_VERSION = "state.v1"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def resp(handler, code, payload):
    body = json.dumps({"schema_version": SCHEMA_VERSION,
                       "generated_at": now_iso(), **payload},
                      ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def load_index():
    return json.loads((DS / "index.json").read_text(encoding="utf-8"))


def case_body_len(c):
    tf = [p for p in c.get("files", {}).get("text", []) if p.endswith("text.txt")]
    if not tf:
        return 0
    p = DS / tf[0]
    if not p.exists():
        return 0
    s = p.read_text(encoding="utf-8", errors="replace")
    s = re.sub(r"^#.*$", "", s, flags=re.M)
    return len(s.strip())


def dir_stats(path: Path, exts):
    n = size = 0
    if path.exists():
        for f in path.rglob("*"):
            if f.is_file() and f.suffix.lower() in exts:
                n += 1
                size += f.stat().st_size
    return {"files": n, "bytes": size}


def quota_summary():
    led = META / "quota_ledger.jsonl"
    per_day = Counter()
    if led.exists():
        for line in led.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    per_day[r.get("date", "?")] += r.get("units", 0)
                except Exception:
                    pass
    days = sorted(per_day)[-7:]
    return {"by_day": [{"date": d, "units": per_day[d]} for d in days],
            "today": per_day.get(datetime.now().strftime("%Y-%m-%d"), 0)}


def harvest_state():
    p = META / "harvest_state.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    d["recent_history"] = (d.get("history") or [])[-5:]
    d["history_total"] = len(d.get("history") or [])
    return d


def state_overview():
    idx = load_index()
    cases = idx["cases"]
    by_platform = Counter(c.get("platform") for c in cases)
    by_topic = Counter(c.get("topic") for c in cases)
    labeled = sum(1 for c in cases if c.get("label"))
    lang = Counter(c.get("lang") for c in cases if c.get("platform") == "twitter")
    media_cases = {"image": dir_stats(DS, {".png", ".jpg", ".jpeg", ".webp"}),
                   "video": dir_stats(DS, {".mp4", ".mkv", ".webm"}),
                   "audio": dir_stats(DS, {".wav", ".m4a", ".mp3"})}
    raw_media = dir_stats(ROOT / "media", {".png", ".jpg", ".jpeg", ".webp",
                                           ".mp4", ".mkv", ".webm", ".wav",
                                           ".m4a", ".mp3", ".vtt"})
    return {
        "index_generated_at": idx.get("generated_at"),
        "case_count": idx.get("count", len(cases)),
        "by_platform": dict(by_platform),
        "by_topic": dict(by_topic),
        "label_coverage": {"labeled": labeled,
                           "unlabeled": len(cases) - labeled},
        "twitter_lang": dict(lang),
        "media": {"dataset_cases": media_cases, "raw_media_dir": raw_media},
        "quota": quota_summary(),
        "harvest": harvest_state(),
    }


def filter_cases(qs):
    idx = load_index()
    cases = idx["cases"]
    for key in ("platform", "topic", "lang", "role"):
        v = qs.get(key, [""])[0]
        if v:
            cases = [c for c in cases if str(c.get(key)) == v]
    v = qs.get("label", [""])[0]
    if v == "true":
        cases = [c for c in cases if c.get("label")]
    elif v == "false":
        cases = [c for c in cases if c.get("label") is None]
    if qs.get("thin", [""])[0] == "1":
        cases = [c for c in cases if case_body_len(c) < 120]
    kw = qs.get("q", [""])[0].lower()
    if kw:
        cases = [c for c in cases
                 if kw in (c.get("id", "") + c.get("title", "")).lower()]
    try:
        limit = min(int(qs.get("limit", ["50"])[0]), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(qs.get("offset", ["0"])[0]), 0)
    except ValueError:
        offset = 0
    slim = [{"id": c["id"], "platform": c.get("platform"),
             "topic": c.get("topic"), "title": (c.get("title") or "")[:80],
             "label": c.get("label"), "lang": c.get("lang"),
             "role": c.get("role"), "url": c.get("url"),
             "body_chars": case_body_len(c)} for c in cases]
    return {"total": len(slim), "limit": limit, "offset": offset,
            "cases": slim[offset:offset + limit]}


def case_detail(cid):
    idx = load_index()
    c = next((x for x in idx["cases"] if x["id"] == cid), None)
    if not c:
        return None
    files = {}
    for mod, paths in c.get("files", {}).items():
        files[mod] = [{"path": p,
                       "exists": (DS / p).exists(),
                       "bytes": (DS / p).stat().st_size if (DS / p).exists() else 0}
                      for p in paths]
    return {**{k: v for k, v in c.items() if k != "files"}, "files": files}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[state_api] %s\n" % (fmt % args))

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = unquote(u.path)
        try:
            if path == "/api/health":
                return resp(self, 200, {"status": "ok"})
            if path == "/api/state":
                return resp(self, 200, state_overview())
            if path == "/api/cases":
                return resp(self, 200, filter_cases(qs))
            m = re.fullmatch(r"/api/cases/([^/]+)", path)
            if m:
                d = case_detail(m.group(1))
                return resp(self, 200, {"case": d}) if d \
                    else resp(self, 404, {"error": "case not found"})
            if path == "/api/harvest":
                return resp(self, 200, {"harvest": harvest_state(),
                                        "quota": quota_summary()})
            return resp(self, 404, {"error": "unknown endpoint",
                                    "endpoints": ["/api/health", "/api/state",
                                                  "/api/cases", "/api/cases/{id}",
                                                  "/api/harvest"]})
        except Exception as e:
            return resp(self, 500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"state_api 只读接口已启动: http://{args.host}:{args.port}"
          f"\n  /api/state  /api/cases  /api/cases/{{id}}  /api/harvest  /api/health"
          f"\nCtrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
