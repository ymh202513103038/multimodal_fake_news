# -*- coding: utf-8 -*-
"""Agent 协议层（agent.v1）——四个 Agent 的输入/输出契约、动作清单、子进程调用器。

为什么单独立一层（而不是直接在 orchestrator 里 import 各脚本）：

1. **主控可替换**。主控既可以是 `orchestrator.py` 里的规则状态机，也可以是外部 LLM。
   两者只有都通过同一份 JSON 契约调用 Agent，"换主控"才不用重写工具层；
   这份契约（任务信封 / 结果信封 / 动作清单）就固定在本文件里，
   `ACTION_MANIFEST` 直接可以当 LLM 的 tool schema 用。
2. **隔离崩溃**。Agent 一律子进程执行：单个采集脚本卡死或抛异常，
   主控仍拿得到超时/退出码/stderr，可以统一裁决与重试，不会一起带走。
3. **只认一行**。Agent 的 stdout 尾部打一行 `RESULT_MARKER + JSON`，
   主控只解析这一行；其余输出是给人看的日志，不参与裁决。
   这样 Agent 内部怎么 print 都不会污染结果。

信封格式见 README 与本文件下方常量。仅用标准库。
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / "prototype"
LOGS_DIR = ROOT / "logs"

# 与 common.py 一致的 UTF-8 输出约定：Windows 控制台默认 GBK，中文日志会乱码
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

SCHEMA_VERSION = "agent.v1"
RESULT_MARKER = "@@AGENT_RESULT@@"

# 四个 Agent 与各自的确定性动作。新增动作必须同时登记到这里，
# 否则 orchestrator 会在入队阶段就拒绝（fail fast，不留半截任务）。
ACTION_MANIFEST: dict[str, dict] = {
    "collect": {
        "desc": "采集：按来源通道拉取真实数据，落 metadata/*.jsonl",
        "actions": {
            "youtube_search": {
                "desc": "YouTube Data API 关键词搜索（消耗配额 100/次）",
                "params": {"query": "关键词", "limit": "条数(int,≤50)",
                           "mock": "true=不调 API 生成确定性样本(bool)"},
            },
            "youtube_media": {
                "desc": "把已采集视频落盘为字幕/音频/720p 视频（零配额）",
                "params": {"what": "subs|audio|video", "limit": "视频数(int)"},
            },
            "youtube_harvest": {
                "desc": "每日轮转关键词收割（读 harvest_state.json 断点）",
                "params": {"queries": "本轮关键词数(int)", "limit": "每词条数(int)",
                           "dry_run": "true=只打印计划(bool)"},
            },
            "x_browser": {
                "desc": "零凭据真浏览器采 X（需 Chrome 在线且已登录）",
                "params": {"keyword": "关键词(英文更稳)", "rounds": "滚动轮数(int)",
                           "limit": "入库条数(int)"},
            },
            "news": {
                "desc": "新闻/核查站采集（模板化，一 URL 一正则即接入新站）",
                "params": {"site": "piyao|snopes|all", "limit": "每站条数(int)"},
            },
            "rebuild_cases": {
                "desc": "把新采到的原始数据组装进案例层 dataset/（采集与归簇之间的必要一环）",
                "params": {"fresh": "true=清空 dataset/ 重建(bool)"},
            },
        },
    },
    "cluster": {
        "desc": "归簇：把案例按主张相似度并成事件簇（一事一簇，解决一谣切多块）",
        "actions": {
            "cluster_cases": {
                "desc": "对 dataset/index.json 做单一链接聚类，输出 metadata/events.json",
                "params": {"threshold": "相似度阈值(float,默认0.30)",
                           "max_size": "单簇上限(int,默认60)", "source": "index|cases"},
            },
        },
    },
    "verify": {
        "desc": "核查：为事件簇生成三层判定（事件/素材/表述）工作表 + 证据缺口预检",
        "actions": {
            "build_packets": {
                "desc": "自动预检只给缺口清单，verdict 一律留空待外部检索",
                "params": {"events": "metadata/events.json 路径(可省)",
                           "event_ids": "逗号分隔的事件 ID(可省=全部)"},
            },
        },
    },
    "card": {
        "desc": "出卡：把事件簇 + 核查包渲染成可发布的案例卡（markdown）",
        "actions": {
            "make_card": {
                "desc": "单事件出卡，落 reports/card_<event_id>.md",
                "params": {"event_id": "事件 ID", "events": "events.json 路径(可省)",
                           "packets": "verify_packets.json 路径(可省)"},
            },
        },
    },
}

AGENTS = tuple(ACTION_MANIFEST)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_task_id(agent: str) -> str:
    return f"{agent}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"


def make_task(agent: str, action: str, params: dict | None = None,
              task_id: str | None = None, timeout_s: int = 900) -> dict:
    """构造任务信封。非法 agent/action 立刻抛 ValueError（入队即失败）。"""
    if agent not in ACTION_MANIFEST:
        raise ValueError(f"未知 Agent: {agent}（可选 {list(ACTION_MANIFEST)}）")
    if action not in ACTION_MANIFEST[agent]["actions"]:
        raise ValueError(f"Agent {agent} 无动作 {action}"
                         f"（可选 {list(ACTION_MANIFEST[agent]['actions'])}）")
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id or new_task_id(agent),
        "agent": agent,
        "action": action,
        "params": params or {},
        "timeout_s": int(timeout_s),
        "created_at": now_iso(),
    }


def make_result(task: dict, status: str = "ok", *, outputs=None, artifacts=None,
                metrics=None, error: str | None = None, next_hint=None,
                elapsed_s: float = 0.0) -> dict:
    """构造结果信封。status ∈ ok / error / skipped。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task.get("task_id"),
        "agent": task.get("agent"),
        "action": task.get("action"),
        "status": status,
        "started_at": task.get("_started_at") or now_iso(),
        "finished_at": now_iso(),
        "elapsed_s": round(elapsed_s, 3),
        "outputs": outputs or {},
        "artifacts": artifacts or [],
        "metrics": metrics or {},
        "error": error,
        "next": next_hint or [],
    }


def emit(result: dict) -> None:
    """Agent 侧收尾：把结果信封作为 stdout 最后一行打出去。"""
    sys.stdout.write(RESULT_MARKER + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def parse_result(stdout: str) -> dict | None:
    """主控侧解析：取最后一行 RESULT_MARKER 之后的内容；取不到返回 None。"""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER):])
            except json.JSONDecodeError:
                return None
    return None


def run_agent(task: dict, *, python: str | None = None,
              timeout_s: int | None = None) -> dict:
    """在子进程里跑一个 Agent，返回结果信封（永不抛异常，失败也返回信封）。

    失败分类：
      - 超时      → status=error, error="timeout"
      - 非零退出  → status=error, error="exit=<code>"，附 stderr 尾部
      - 无结果行  → status=error, error="no_result_marker"（Agent 崩了但退出码为 0）
    """
    t0 = now_iso()
    timeout = int(timeout_s or task.get("timeout_s") or 900)
    py = python or sys.executable
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    task_file = LOGS_DIR / f".task_{task['task_id']}.json"
    task_file.write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")

    cmd = [py, str(PROTOTYPE / "agents.py"), "--task-file", str(task_file)]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        code = -1
        res = make_result(task, "error", error=f"timeout>{timeout}s")
    except FileNotFoundError as e:
        return make_result(task, "error", error=f"python_not_found: {e}")
    finally:
        try:
            task_file.unlink()
        except OSError:
            pass

    if code == 0:
        parsed = parse_result(out)
        if parsed:
            return parsed
        res = make_result(task, "error", error="no_result_marker",
                          metrics={"stdout_tail": (out or "")[-400:]})
    elif code == -1:
        pass  # 超时分支已构造 res
    else:
        # 非零退出时也要先认结果行：Agent 通常"报了错再退出 1"，
        # 信封里的 error 比裸 exit 码有用得多；解析不到才退回退出码。
        parsed = parse_result(out)
        if parsed:
            parsed["_exit_code"] = code
            return parsed
        res = make_result(task, "error", error=f"exit={code}",
                          metrics={"stderr_tail": (err or "")[-600:]})
    res["_started_at"] = t0
    res["_cmd"] = " ".join(str(c) for c in cmd)
    res["_stdout_tail"] = (out or "")[-1500:]
    return res


def manifest_json() -> str:
    """动作清单（可直接喂给 LLM 当 tool schema）。"""
    return json.dumps({"schema_version": SCHEMA_VERSION,
                       "agents": ACTION_MANIFEST}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(manifest_json())
