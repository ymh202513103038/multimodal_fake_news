# 标注规范（试点 v0.1）

## 字段（schema）

| 字段 | 取值 | 说明 |
|---|---|---|
| `is_fake` | 1 / 0 / -1 | 1=虚假；-1=不确定（进复核池）；0=真实 |
| `modality_type` | text_rumor / doctored_image / out_of_context_video / mixed / other | 主要造假模态 |
| `evidence_url` | URL | 对应的事实核查报告或原始出处 |
| `rationale` | 一句话 | 判定理由（便于计算标注一致性） |
| `annotator` / `labeled_at` | 字符串 | 标注人 / 时间 |

## 流程

1. 试点 20~30 条，**双人独立标注** → 计算 Cohen's kappa；
2. kappa < 0.6 时修订本规范再标，不直接扩量；
3. 证据优先：找不到 `evidence_url` 的样本标 -1 进复核池，不硬判。

## 参考

- **GroundLie360**（[ACM MM 2025](https://dl.acm.org/doi/10.1145/3746027.3758191)，
  [arXiv:2509.08008](https://ar5iv.labs.arxiv.org/html/2509.08008)）：多模态虚假信息的
  「定位/取证式」标注——除判真假外，还标注被篡改/断章的具体元素（grounding）。
  建议**借其 schema 设计我们的 evidence 字段**，一期不照搬全量；
- FakeNewsNet（PolitiFact / GossipCop 的声明-推文对齐）可作为公开数据兜底。
