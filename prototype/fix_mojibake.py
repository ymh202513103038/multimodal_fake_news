# -*- coding: utf-8 -*-
"""修复双重编码乱码（mojibake）扫描器。

背景：patch_factcheck_bodies.py 初版抓 piyao 页面时未处理响应缺 charset 的情况，
requests 按 HTTP 规范把 UTF-8 字节按 ISO-8859-1 解码，写盘后成为
"é¦é¡µ"式双重编码乱码。本脚本扫描 dataset/ 全部文本文件与 metadata/ 的
jsonl/json，识别并修复可逆的 latin-1<->utf-8 双重编码。

修复原理：正确读出的字符串若满足
  s.encode("latin-1") 成功 且 再按 utf-8 解码成功 且 结果含 CJK
则 s 本身是被误解码的产物，用 utf-8 版本替换。

用法：python prototype/fix_mojibake.py [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CJK = lambda s: any("\u4e00" <= ch <= "\u9fff" for ch in s)


def try_fix(s: str):
    """返回 (修复后文本, 是否修复)。非乱码原样返回。"""
    if not s or not looks_mojibake(s):
        return s, False
    try:
        repaired = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s, False
    if repaired != s and CJK(repaired):
        return repaired, True
    return s, False


def looks_mojibake(s: str) -> bool:
    """疑似乱码启发式：含 latin 扩展区字符（乱码的典型形状）"""
    return any("\u00c0" <= ch <= "\u024f" for ch in s)


def fix_text_file(p: Path, dry: bool):
    raw = p.read_bytes()
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"跳过(非UTF-8): {p}"
    fixed, changed = try_fix(s)
    if changed and not dry:
        p.write_bytes(fixed.encode("utf-8"))
    return f"{'已修复' if changed else '正常'}: {p.relative_to(ROOT)}"


def fix_jsonlike(p: Path, dry: bool):
    """jsonl: 逐行 json.loads 后修字符串字段再写回; json: 整体解析"""
    raw = p.read_text(encoding="utf-8")
    changed_any = False
    if p.suffix == ".jsonl":
        out_lines = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            def walk(v):
                nonlocal changed_any
                if isinstance(v, str):
                    f, c = try_fix(v)
                    changed_any = changed_any or c
                    return f
                if isinstance(v, list):
                    return [walk(x) for x in v]
                if isinstance(v, dict):
                    return {k: walk(x) for k, x in v.items()}
                return v
            rec = walk(rec)
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        if changed_any and not dry:
            p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        return f"{'已修复' if changed_any else '正常'}: {p.relative_to(ROOT)}"
    else:
        rec = json.loads(raw)
        def walk(v):
            nonlocal changed_any
            if isinstance(v, str):
                f, c = try_fix(v)
                changed_any = changed_any or c
                return f
            if isinstance(v, list):
                return [walk(x) for x in v]
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return v
        rec = walk(rec)
        if changed_any and not dry:
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        return f"{'已修复' if changed_any else '正常'}: {p.relative_to(ROOT)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ds = ROOT / "dataset"
    targets = list(ds.rglob("text.txt")) + list(ds.rglob("reason.txt")) \
        + list(ds.rglob("source.txt"))
    meta = list((ROOT / "metadata").rglob("*.jsonl")) + \
        [ROOT / "metadata" / "case_names.json", ROOT / "dataset" / "index.json"]

    fixed = normal = other = 0
    for p in targets:
        msg = fix_text_file(p, args.dry_run)
        if msg.startswith("已修复"):
            fixed += 1
            print(msg)
        elif msg.startswith("正常"):
            normal += 1
        else:
            other += 1
            print(msg)
    for p in meta:
        if not p.exists():
            continue
        msg = fix_jsonlike(p, args.dry_run)
        if msg.startswith("已修复"):
            fixed += 1
            print(msg)
        else:
            normal += 1

    print(f"\n扫描完成: 修复 {fixed}, 正常 {normal}, 异常跳过 {other}"
          + ("  (dry-run 未写盘)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
