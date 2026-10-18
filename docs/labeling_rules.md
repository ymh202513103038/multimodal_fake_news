# 标注规范（试点 v0.2 —— 对齐 GroundLie360 三级标注）

> 参考：**GroundLie360**（ACM MM 2025，[repo](https://github.com/yangbingjian/GroundLie360)，
> [arXiv:2509.08008]）。其真实视频数据集采用三级标注：二分类 → 多标签造假类型 →
> 跨模态定位（grounding），并以 Snopes 证据与标注员推理做验证。本规范即按此对齐。

## L1 二分类（每条必标）

| 字段 | 取值 | 说明 |
|---|---|---|
| `is_fake` | 1 / 0 / -1 | 1=虚假；0=真实；-1=不确定（进复核池，不硬判） |

## L2 多标签造假类型（is_fake=1 时必标，可多选，分号分隔）

| 类型 | 含义 | 对应旧字段 |
|---|---|---|
| `false_title` | 标题含可证伪的虚假断言 | text_rumor |
| `false_speech` | 口播/说话内容含虚假断言 | text_rumor |
| `temporal_edit` | 剪辑篡改时间线、拼接误导叙事 | mixed |
| `cgi` | 数字合成 / AI 生成画面 | doctored_image |
| `contradictory_content` | 文-视频/图文语义不匹配（断章取义） | out_of_context_video |
| `unsupported_content` | 标题主张在内容中无证据支持 | other |

字段名：`fake_types`，例：`false_title;false_speech`

## L3 多模态定位 grounding（对"假"样本尽量填，试点可留空进复核）

| 字段 | 格式 | 说明 |
|---|---|---|
| `grounding_title` | 起止字符位置 + 原文片段 | 标题中误导片段，如 `[13-33] "48 Hour Challenge"` |
| `grounding_speech` | 起止秒（视频时间轴）+ 引文 | 口播中误导片段，如 `[0-10.2] "It's called the 48 hour challenge"` |
| `grounding_visual` | 时间点或帧号 + 描述（bbox 二期） | 画面中的 CGI/篡改元素位置 |

（GroundLie360 原始格式：title span 用字符区间，speech 用秒区间数组，
visual 用逐帧 bbox——见其 `data.csv` / `bbox.csv`。我们试点先记"人可读"版本，
数据集 v1 再转机读格式。）

## 证据链字段（对齐其 debunking / original / evidence 配对思想）

| 字段 | 说明 |
|---|---|
| `evidence_url` | 事实核查报告 / 原始出处链接 |
| `event` | （index 层）同一事件的真假对/辟谣对归组标识 |

配套采集思想：同一事件尽量收集 **目标样本 + 原始样本 + 辟谣内容 + 佐证材料**
（GroundLie360 的 curation 四分类），在 dataset/index.json 的 `event` 字段归组。

## 通用字段

| 字段 | 说明 |
|---|---|
| `rationale` | 标注员推理（一句话到几句话）——**必填**，这是 grounding 思想的核心 |
| `modality_type` | 主要造假模态速记（保留兼容）：text_rumor / doctored_image / out_of_context_video / mixed / other |
| `annotator` / `labeled_at` | 标注人 / 时间 |

## 流程（不变）

1. 试点 20~30 条，**双人独立标注** → Cohen's kappa ≥ 0.6 才放量；
2. 证据优先：找不到 `evidence_url` 的样本 is_fake=-1 进复核池；
3. 顺序建议：先 L1+rationale（快），L2 对假样本标，L3 复核阶段补。

## 工具

- 标注表生成：`python prototype\make_label_sheet.py`（列即本规范）
- 生成后自动进入 dataset/ 案例的 reason/ 与 index 的 label/reason 字段
  （`make_cases.py` 自动回填）。
