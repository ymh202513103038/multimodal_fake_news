# LLM 主控设计稿（第 5 步升级路径，2026-11-04）

> 状态：**设计稿，未实施**。规则主控（orchestrator.py）保留作兜底与回归对照。
> 本文回答三个问题：LLM 主控的决策循环长什么样、护栏怎么设、怎么验收。

## 1. 定位与铁律

LLM 主控不是替换工具层，而是替换**"决定下一步做什么"**这一层。
四条铁律（与项目方法同源，写进 system prompt）：

1. **来源归属**：你只能引用已落盘的核查原文或检索到的可点开原文，禁止凭知识直接判真假；
2. **insufficient 是正常产出**：抽不到就标缺口，禁止为了让覆盖率好看而放宽词表；
3. **预算刚性**：每次 run 有 API 调用与采集配额上限（quota_ledger 是硬约束）；
4. **审计完备**：每个决策（含"决定什么都不做"）落 runs.jsonl，可回放。

## 2. 决策循环（观察 → 候选 → 决策 → 执行 → 审计）

```
┌────────────────────────────────────────────────────────┐
│ ① 观察：GET /api/state + tasks.json + 事件卡缺口摘要     │
│    （载荷控制：state_api 已按小载荷设计，事件卡只发缺口码）│
│ ② 候选：ACTION_MANIFEST = tool schema（已就绪）          │
│    + external_findings.jsonl 摘要（哪些缺口已回填）       │
│ ③ 决策：LLM 输出 JSON：                                  │
│    {"actions":[{"agent","action","params","why"}],       │
│     "stop":false, "budget_used_estimate":N}              │
│ ④ 护栏校验（§4）→ orchestrator submit（复用现有入队）     │
│ ⑤ 执行：子进程跑 Agent（与规则主控完全同一条执行路径）    │
│ ⑥ 审计：决策+结果落 runs.jsonl（新事件类型 decision）    │
└────────────────────────────────────────────────────────┘
```

关键设计：**LLM 只产出"任务信封序列"，执行 100% 走现有 orchestrator submit → agents.py 子进程**。
这样：执行语义、退避、审计、断点续跑全部复用，LLM 出错最多产生"坏任务"，不会破坏流水线。

## 3. 两种模式

| 模式 | 行为 | 适用 |
|---|---|---|
| **建议模式**（先做） | LLM 产出计划 → 人工看 `why` → 确认后 submit | 前期验证、演示、不可逆采集前 |
| **自治模式**（后做） | 预算内自动循环（默认单轮 ≤5 个任务 / ≤1 次采集），超限暂停等人 | 例行扩数据集 |

## 4. 护栏（逐条可测）

| 护栏 | 规则 | 校验方式 |
|---|---|---|
| 动作白名单 | agent/action 必须在 ACTION_MANIFEST 内 | orchestrator 已有路由校验（复用） |
| 参数 schema | params 按 Manifest 声明校验（类型/枚举） | 新增 jsonschema 校验，失败即拒 |
| 采集预算 | youtube 采集单轮 ≤1 次、≤limit 上限；news ≤2 次 | 决策层拒绝 + quota_ledger 双保险 |
| 危险动作 | `--fresh`、删除类参数在自治模式禁用 | 白名单排除 |
| 预算熔断 | 单轮 LLM 调用 ≤3 次、token 记账 | 决策循环计数 |
| 空转熔断 | 连续 2 轮决策"无可做任务" → 停并输出报告 | 决策历史对比 |
| 幂等 | 相同任务信封 60s 内重复 → 去重拒绝 | task_id 内容哈希 |

## 5. 提示词骨架（v0，实施时放 prompt/ 目录）

```
你是「真途」流水线的调度主控。你的职责：看状态、选下一步、说明理由。
项目铁律：①来源归属，禁止凭知识判真假；②insufficient 是正常产出；
③预算刚性；④每个决策必须带 why（一句话）。
可用动作见 tools（ACTION_MANIFEST）。当前状态见输入。
输出严格 JSON（schema 见下），不要输出别的。
优先级启发：no_label→提示人工；no_source_evidence→先查 external_findings
是否已覆盖，未覆盖且属高价值簇才建议检索；everything done→stop。
```

模型产出后本地用 jsonschema 校验；连续 2 次 schema 不合格 → 熔断回退规则主控。

## 6. 验收（对齐 orchestration.md §9.1）

**同队列对照**：同一批入队任务，分别用规则主控与 LLM 主控（建议模式）执行，
比对：①产物文件集合一致；②runs.jsonl 成功率一致；③LLM 的 why 是否提供
规则主控没有的**有效**下一步（人工评 5 条抽样）。三者全过才算升级成功，
规则主控保留为 CI 回归基线。

## 7. 实施 5 步（预计 2~3 个工作块）

1. `prototype/orchestrator_llm.py`（新文件，不动 orchestrator.py）：
   读 state → 组 prompt → 调模型 → schema 校验 → 护栏 → submit；
2. `prompt/` 目录：system prompt + few-shot（3 个历史决策案例）；
3. runs.jsonl 增 decision 事件类型（agent="llm_controller"）；
4. `--mode suggest|auto` 开关；建议模式输出 `metadata/llm_plan.md`（人读）；
5. 对照验收（§6）→ 文档回写 orchestration.md。

## 8. 未决问题（实施前要拍板）

- 模型与接入方式（本机 API？代理？——网络环境见 roadmap"网络提示"）；
- 自治模式的预算数值（建议先 1 采集/轮、5000 units/天，跑一周看账本）；
- 建议模式的确认入口（CLI 逐条 y/n，还是生成 xlsx 清单批量勾选）。
