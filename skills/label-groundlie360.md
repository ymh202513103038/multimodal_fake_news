---
name: groundlie360-labeling
description: 多模态虚假新闻三级标注 SOP（对齐 GroundLie360）——从标注表生成到案例回填的完整流程
trigger: 一批新采集样本进入 metadata/ 且需要人工标注时；或用户提到"打标/标注/贴标签"
inputs:
  meta_file: metadata/ 下的 *_meta.jsonl（样本池）
  samples: 试点条数（建议 20~30）
outputs:
  - metadata/label_sheet.csv（标注表，annotator 填写）
  - dataset/ 案例的 label/reason 字段（make_cases 自动回填）
guardrails:
  - 仅学术研究；标注对象为公开内容
  - kappa < 0.6 禁止扩量
  - 无证据链的样本 is_fake=-1 进复核池，禁止硬判
acceptance: 双人标注 kappa ≥ 0.6；金标准确率 ≥ 95%；全量样本 reason 非空
changelog: v1 2026-10-18 对齐 GroundLie360 三级标注（二分类/多标签/grounding）
---

# GroundLie360 式三级标注 SOP

## 背景

GroundLie360（ACM MM 2025）提出虚假信息 **grounding（定位）** 任务：不仅判真假，
还要指出"假在哪"——标题哪个词、口播哪一秒、画面哪个元素。我们的标注体系与其对齐。

## 步骤

### 1. 生成标注表
```powershell
python prototype\make_label_sheet.py --samples 20            # 默认从 youtube_meta 抽
python prototype\make_label_sheet.py --meta metadata\x\deepfake__2026-10-18.jsonl --source x --samples 10
```
列结构 = documentation/labeling_rules.md v0.2：L1 is_fake → L2 fake_types →
L3 grounding_title/speech/visual → 证据链 → rationale。

### 2. 标注顺序（省力原则）
1. 全员先标 **L1 is_fake + rationale**（一句话理由必填）；
2. is_fake=1 的样本补 **L2 fake_types**（6 选多：false_title / false_speech /
   temporal_edit / cgi / contradictory_content / unsupported_content，分号分隔）；
3. 复核阶段对存疑样本补 **L3 grounding**（标题字符区间 / 口播秒区间 / 画面描述）。

### 3. 质量控制
- 双人独立盲标 → `sklearn.metrics.cohen_kappa_score` 计算 κ；
- κ < 0.6 → 逐条讨论分歧 → 修订 documentation/labeling_rules.md → 重标；
- 批次内混 5% 金标题，个人金标准确率 < 95% 该批回炉。

### 4. 回填数据集
标注完成后重跑 `python prototype\make_cases.py`，
label/reason 自动写入 dataset/ 案例的 reason/reason.txt 与 index.json。

## 参考
- GroundLie360: https://github.com/yangbingjian/GroundLie360
  （data.csv 字段：first_level=二分类，second_level=多标签，
  third_level=title span 定位，false_speech_temporal=口播秒区间，bbox.csv=逐帧视觉定位）
- 本项目规范：documentation/labeling_rules.md（v0.2）
