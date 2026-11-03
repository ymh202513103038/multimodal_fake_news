# -*- coding: utf-8 -*-
"""生成人工标注用的 Excel 工作簿（试点 20 条，双人独立标，用于算 kappa）。

为什么要有这个脚本而不是手工做表：
  - 每次试点/扩量都要一份**同结构**的表（列名与 labeling_rules.md v0.2 对齐），
    手工做必然漂移，漂移了就没办法算一致性；
  - 抽样必须可复现（固定 seed），否则"这批标了哪 20 条"说不清；
  - 表里自带判定口径，标注员不用来回翻文档。

依赖 openpyxl（本机全局 Python 3.10 已装；venv 里没装，故用全局 python 运行）：
  python prototype\\make_label_workbook.py
  python prototype\\make_label_workbook.py --samples 20 --seed 7 --out metadata\\标注表_试点20条.xlsx

输出结构：
  怎么标        8 步流程 + 时间预算 + 三条纪律
  判定口径      L1/L2/L3 的定义与枚举值（照抄 labeling_rules.md v0.2）
  示例          一条填满的示范（含理由写法），照着格式填
  标注表        N 行待填，is_fake / fake_types 带下拉
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "dataset"
INDEX = DS / "index.json"

L2_TYPES = ["false_title", "false_speech", "temporal_edit", "cgi",
            "contradictory_content", "unsupported_content"]

HEADERS = [
    ("sample_id", 26), ("平台", 9), ("标题", 46), ("正文摘要", 52),
    ("有媒体", 7), ("原帖链接", 34),
    ("is_fake\n1假/0真/-1拿不准", 16), ("fake_types\n可多选;分号隔开", 26),
    ("grounding_title\n标题片段", 22), ("grounding_speech\n秒区间", 18),
    ("grounding_visual\n画面描述", 22), ("evidence_url\n核查/证据", 30),
    ("rationale\n理由", 40), ("annotator", 12), ("labeled_at\nYYYY-MM-DD", 14),
]

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="E8EEF7")
NEED_FILL = PatternFill("solid", fgColor="FFF3CD")   # 必须填的列
TIP_FONT = Font(size=10, color="555555")


def load_cases() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["cases"]


def body_of(case: dict) -> str:
    paths = [p for p in case.get("files", {}).get("text", []) if p.endswith("text.txt")]
    if not paths:
        return ""
    p = DS / paths[0]
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"^#.*$", "", t, count=1, flags=re.M)
    t = html.unescape(t)          # 字幕里的 &gt;&gt; / &amp; 不清掉，标注员会读成乱码
    return re.sub(r"\s+", " ", t).strip()[:600]


def has_media(case: dict) -> str:
    for k in ("image", "video", "wav"):
        if case.get("files", {}).get(k):
            return "是"
    return "否"


def pick(cases: list[dict], n: int, seed: int) -> list[dict]:
    """分层抽样：一半核查源（有证据可查），一半社媒（要自己判断）。"""
    fc = [c for c in cases if c.get("platform") in ("snopes", "piyao")]
    soc = [c for c in cases if c.get("platform") not in ("snopes", "piyao")]
    rng = random.Random(seed)
    rng.shuffle(fc)
    rng.shuffle(soc)
    half = n // 2
    out = fc[:half] + soc[:n - half]
    rng.shuffle(out)
    return out


def sheet_howto(wb: Workbook) -> None:
    ws = wb.create_sheet("怎么标")
    ws.column_dimensions["A"].width = 118
    lines = [
        ("人工标注流程（试点 20 条 · 双人独立标 · 算 Cohen's kappa）", True),
        ("", False),
        ("1. 打开「标注表」，从头到尾**独立**标完 20 行。不要看另一个人标的结果——", False),
        ("   看了就失去一致性检验的意义（kappa 会虚高）。", False),
        ("2. 每行只填 6 个黄色列：is_fake、fake_types、grounding_* 三列、evidence_url、rationale；", False),
        ("   annotator 和 labeled_at 别忘了填。", False),
        ("3. is_fake：1=这条主张是假的/误导的；0=主张成立；-1=拿不准（**允许且鼓励**用 -1，", False),
        ("   硬猜会污染 kappa；拿不准的写进 rationale 说明卡在哪一层）。", False),
        ("4. fake_types（可多选，分号隔开）：只在 is_fake=1 时填。", False),
        ("   6 个值见「判定口径」—不要自己造词，造词会让统计对不上。", False),
        ("5. grounding_*：能指到哪就填哪，指不到留空。", False),
        ("   title=标题里的片段原文；speech=口播秒区间（mm:ss-mm:ss）；visual=画面位置/内容描述。", False),
        ("6. evidence_url：能支撑你判断的**核查页或原始报道**链接；没有就留空，不要贴原帖链接充数。", False),
        ("7. rationale：一句话说清「哪一层假、凭什么」。参考「示例」页的写法。", False),
        ("8. 存盘后把文件发回，我用 prototype\\score_labels.py 算 kappa 并出分歧清单。", False),
        ("", False),
        ("时间预算：一条 3–5 分钟（核查源案例快，社媒案例慢），20 条约 60–100 分钟。", False),
        ("", False),
        ("三条纪律（比标注速度重要）", True),
        ("• 先判层，再判真假：先问「事件发生了吗」→「画面是这个事件的吗」→「说法有没有超出证据」，", False),
        ("  最后才落 is_fake。跳过层直接判真假，就会把「事件真+表述夸大」标成 0，这是本项目最想避免的错。", False),
        ("• 不知道就写 -1 并在 rationale 里写清缺哪一层证据，这比猜一个值有信息量。", False),
        ("• 不要用「这条看起来像假的」当理由；理由里必须能指到具体层与具体依据。", False),
        ("", False),
        ("这份表怎么来的", True),
        ("由 prototype\\make_label_workbook.py 固定 seed 抽样生成，可随时复现；", False),
        ("案例内容取自 dataset/（六件套 + index.json），未做任何改写。", False),
    ]
    for i, (text, bold) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            c.font = Font(bold=True, size=12)
        elif text.startswith("•") or text.startswith("   "):
            c.font = TIP_FONT
    ws.sheet_view.showGridLines = False


def sheet_rules(wb: Workbook) -> None:
    ws = wb.create_sheet("判定口径")
    rows = [
        ("层级", "字段", "取值", "含义"),
        ("L1 二分类", "is_fake", "1", "该主张是假的/误导的（含夸大、错配、旧素材冒充）"),
        ("", "", "0", "该主张成立（事实、表述、素材都对得上）"),
        ("", "", "-1", "拿不准 / 证据不足（填理由说明缺哪层）"),
        ("L2 多标签", "fake_types", "false_title", "标题与正文内容不符（标题党、断章取义）"),
        ("", "", "false_speech", "口播内容造假（伪造引述、错配音）"),
        ("", "", "temporal_edit", "时间/剪辑造假（旧视频当新、剪掉关键句、变速）"),
        ("", "", "cgi", "AI/合成/特效生成（deepfake、AI 配图）"),
        ("", "", "contradictory_content", "图文/音画互相矛盾，或素材与事件错配"),
        ("", "", "unsupported_content", "强断言无任何证据支撑（彻底摧毁/全部投降类）"),
        ("L3 定位", "grounding_title", "文本片段", "标题里造假的原文片段（原样摘录）"),
        ("", "grounding_speech", "mm:ss-mm:ss", "口播造假的秒区间（需能定位到视频）"),
        ("", "grounding_visual", "文本描述", "画面造假的描述（哪一帧/哪块区域、怎么假）"),
        ("证据", "evidence_url", "URL", "支撑判断的核查页/原始报道；没有就留空"),
        ("理由", "rationale", "一句话", "哪一层假 + 凭什么（呼应上面选的值）"),
    ]
    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
            if r == 1:
                cell.font = Font(bold=True)
                cell.fill = HEAD_FILL
    for col, w in zip("ABCD", (12, 18, 16, 62)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


def sheet_example(wb: Workbook) -> None:
    ws = wb.create_sheet("示例")
    ctx = [
        ("sample_id", "factcheck__snopes_002"),
        ("平台", "snopes"),
        ("标题", "Ilhan Omar didn't flee to Morocco after federal agents' raid on her home"),
        ("正文摘要", "Snopes 评级 False；原文另指出同账号发布了一张 AI 生成图片，称其躲在地下隧道。"),
        ("有媒体", "是"),
        ("原帖链接", "https://www.snopes.com/fact-check/ilhan-omar-flee-morocco/"),
        ("is_fake", 1),
        ("fake_types", "cgi;contradictory_content"),
        ("grounding_title", "Ilhan Omar fled to Morocco"),
        ("grounding_speech", ""),
        ("grounding_visual", "配图为 AI 生成，用于佐证‘躲在地下隧道’的说法"),
        ("evidence_url", "https://www.snopes.com/fact-check/ilhan-omar-flee-morocco/"),
        ("rationale", "事件层不实（Rating=False）；素材层另假（AI 生成图冒充现场），故标 cgi+contradictory_content"),
        ("annotator", "示例（不要照抄这条）"),
        ("labeled_at", "2026-11-03"),
    ]
    ws.cell(row=1, column=1, value="字段").font = Font(bold=True)
    ws.cell(row=1, column=2, value="填好的样子").font = Font(bold=True)
    ws.cell(row=1, column=1).fill = HEAD_FILL
    ws.cell(row=1, column=2).fill = HEAD_FILL
    for r, (k, v) in enumerate(ctx, 2):
        ws.cell(row=r, column=1, value=k).border = BORDER
        c = ws.cell(row=r, column=2, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.border = BORDER
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 96
    ws.freeze_panes = "A2"


def sheet_table(wb: Workbook, cases: list[dict], n: int, seed: int) -> None:
    ws = wb.active
    ws.title = "标注表"
    for c, (name, width) in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = Font(bold=True, size=9)
        cell.fill = NEED_FILL if name.split("\n")[0] in (
            "is_fake\n1假/0真/-1拿不准", "fake_types\n可多选;分号隔开", "grounding_title\n标题片段",
            "grounding_speech\n秒区间", "grounding_visual\n画面描述", "evidence_url\n核查/证据",
            "rationale\n理由", "annotator", "labeled_at\nYYYY-MM-DD") else HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[1].height = 42

    for r, case in enumerate(pick(cases, n, seed), 2):
        body = body_of(case)
        vals = [case.get("id"), case.get("platform"), case.get("title"),
                body, has_media(case), case.get("url"),
                None, None, None, None, None, None, None, None, None]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
            cell.font = Font(size=9)

    # 下拉：is_fake 1/0/-1；fake_types 六值提示（多选需手打，故给提示不强制）
    last = n + 1
    dv1 = DataValidation(type="list", formula1='"1,0,-1"', allow_blank=True,
                         showErrorMessage=True, errorTitle="只能填 1 / 0 / -1",
                         error="1=假 0=真 -1=拿不准")
    ws.add_data_validation(dv1)
    dv1.add(f"G2:G{last}")
    dv2 = DataValidation(type="list", formula1='"' + ",".join(L2_TYPES) + '"',
                         allow_blank=True, showErrorMessage=False)
    ws.add_data_validation(dv2)
    dv2.add(f"H2:H{last}")
    ws.freeze_panes = "C2"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成人工标注工作簿")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "metadata" / "标注表_试点20条.xlsx"))
    args = ap.parse_args()

    cases = load_cases()
    if not cases:
        print("dataset/index.json 为空")
        return 1
    wb = Workbook()
    sheet_howto(wb)
    sheet_rules(wb)
    sheet_example(wb)
    sheet_table(wb, cases, args.samples, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"已生成 {out}（{args.samples} 条，seed={args.seed}）")
    print(f"生成时间 {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
