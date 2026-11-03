# -*- coding: utf-8 -*-
r"""三层判定规则集的回归测试（无需 pytest，直接 python 运行）。

守的是什么：这几个断言是"方法没被改坏"的最小集合——
  - False 评级必须落到事件层+表述层；
  - Mixture 必须拆开（事件层真 / 表述层假），这是三层判定的存在理由；
  - Miscaptioned 必须只判表述层（事件层不许硬凑结论）；
  - 中文辟谣站的版式句（"e起辟谣 清朗有我"）不许被当成核查结论。

改动 evidence_rules.py 的词表/解析后跑一遍：
  fake_news_env\Scripts\python.exe prototype\test_evidence_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import evidence_rules as rules

DS = Path(__file__).resolve().parents[1] / "dataset"


def _members(case_id: str, platform: str) -> list[dict]:
    body = (DS / case_id / "text" / "text.txt").read_text(encoding="utf-8", errors="replace")
    return [{"case_id": case_id, "platform": platform,
             "title": body.splitlines()[0][:90], "body": body,
             "parsed": rules.parse_snopes(body) if platform == "snopes" else {}}]


def check_false_with_ai_media():
    """snopes_002：评级 False，且原文点名 AI 生成图片 → 事件层假 + 素材层不符。"""
    layers = rules.judge_layers(_members("factcheck__snopes_002", "snopes"))
    assert layers["event_layer"]["verdict"] == "source_says_false", layers["event_layer"]
    assert layers["claim_layer"]["verdict"] == "source_says_false", layers["claim_layer"]
    assert layers["media_layer"]["verdict"] == "source_says_media_mismatch", layers["media_layer"]
    assert layers["media_layer"]["kind"] == "ai_or_synthetic"
    assert "AI generated" in layers["media_layer"]["evidence"][0]["quote"]


def check_mixture_splits_layers():
    """snopes_005：Mixture 必须拆成"事件真 + 表述假"，不许压成一个数。"""
    layers = rules.judge_layers(_members("factcheck__snopes_005", "snopes"))
    assert layers["event_layer"]["verdict"] == "source_says_true", layers["event_layer"]
    assert layers["claim_layer"]["verdict"] == "source_says_false", layers["claim_layer"]
    ev_q = layers["event_layer"]["evidence"][0]["quote"]
    cl_q = layers["claim_layer"]["evidence"][0]["quote"]
    assert ev_q != cl_q, "事件层与表述层引用了同一句，等于没分层"
    assert layers["event_layer"]["evidence"][0]["field"] == "What's True"
    assert layers["claim_layer"]["evidence"][0]["field"] == "What's False"


def check_miscaptioned_leaves_event_open():
    """snopes_007：Miscaptioned = 素材/说明错，事件层必须留 insufficient。"""
    layers = rules.judge_layers(_members("factcheck__snopes_007", "snopes"))
    assert layers["claim_layer"]["verdict"] == "source_says_false", layers["claim_layer"]
    assert layers["event_layer"]["verdict"] == "insufficient", layers["event_layer"]


def check_piyao_noise_filtered():
    """piyao_007：正文只有活动倡议与口号，不许产出任何判定。"""
    layers = rules.judge_layers(_members("factcheck__piyao_007", "piyao"))
    assert all(v["verdict"] == "insufficient" for v in layers.values()), layers


def check_no_source_stays_insufficient():
    """X 推文案例：无核查源，任何层都不许给"来源称"结论。"""
    body = (DS / "x_fake_news__tweet_8161315901" / "text" / "text.txt")
    if not body.exists():
        return
    m = [{"case_id": "x_fake_news__tweet_8161315901", "platform": "twitter",
          "title": body.read_text(encoding="utf-8", errors="replace")[:90],
          "body": body.read_text(encoding="utf-8", errors="replace"), "parsed": {}}]
    layers = rules.judge_layers(m)
    assert layers["event_layer"]["verdict"] == "insufficient"
    assert layers["media_layer"]["verdict"] == "insufficient"


def main() -> int:
    tests = [check_false_with_ai_media, check_mixture_splits_layers,
             check_miscaptioned_leaves_event_open, check_piyao_noise_filtered,
             check_no_source_stays_insufficient]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
