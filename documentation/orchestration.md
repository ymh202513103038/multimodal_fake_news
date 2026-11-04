# 多智能体调度层（第 4 步）

> 状态：**已落地并实测**（2026-11-03）。此前项目的四层里，工具层全部就绪，
> 唯独"谁来按什么顺序调、失败了怎么办"没做——这一层补的就是这个缺口。

## 1. 为什么需要这一层

之前项目里有 20 多个脚本，每个都能单独跑。这带来三个具体问题：

1. **顺序靠人记**：采集完得记得先 `make_cases` 才能 `cluster`，忘了就出现
   "新采的数据进不了下游"——第一次做这一层时就踩在这个坑里（见 §7.1）。
2. **失败靠人看**：429、代理抖动、登录态掉线，全靠盯屏幕；没有退避、没有封顶、
   没有"连续失败就停"的规则（X 通道连续 401 还硬跑是要被风控的）。
3. **断点靠人续**：跑到一半断了，下次从哪儿接着跑，没有落盘的状态。

调度层把这三件事变成代码：**队列落盘、路由显式、失败有策略**。

## 2. 架构

```
orchestrator.py            调度主控（规则状态机；可换成 LLM，契约不变）
  ├── 队列 metadata/tasks.json     状态机 + 依赖 + 退避 + 断点续跑
  ├── 路由 agent_protocol.ACTION_MANIFEST   唯一真源：有哪些 Agent/动作/参数
  └── 子进程调用 agents.py ──┬── collect  采集（5 通道 + 案例层组装）
                             ├── cluster  归簇
                             ├── verify   核查（三层判定工作表 + 缺口预检）
                             └── card     出卡（markdown 案例卡）
```

**Agent 一律子进程执行**：单个采集脚本卡死或崩溃，主控仍拿得到超时/退出码/错误行，
可以统一裁决与重试，不会一起被带走。

## 3. 契约（agent.v1）

任务信封（主控 → Agent）：

```json
{"schema_version":"agent.v1","task_id":"cluster-20261103191904-3b4c",
 "agent":"cluster","action":"cluster_cases",
 "params":{"threshold":0.45},"timeout_s":900,"created_at":"..."}
```

结果信封（Agent → 主控，stdout **最后一行**，前缀 `@@AGENT_RESULT@@`）：

```json
{"schema_version":"agent.v1","task_id":"...","agent":"cluster","action":"cluster_cases",
 "status":"ok","elapsed_s":0.096,
 "outputs":{"event_count":96,"merges":2},"artifacts":["metadata/events.json"],
 "metrics":{"cases":98,"events":96},"error":null,"next":["verify"]}
```

三条约定，都有具体理由：

- **只认最后一行**：Agent 内部怎么 `print` 都不会污染裁决。
- **非零退出也先认结果行**：Agent 通常"报了错再退出 1"，信封里的 `error`
  比裸退出码有用得多。实测改进前后：
  `exit=1` → `exit=2｜news_probe.py: error: argument --site: invalid choice: 'bogus'`。
- **`next` 字段**：Agent 自己建议下一步。规则主控目前不用它，但它是给 LLM 主控
  预留的决策线索——工具比主控更清楚自己之后该接什么。

## 4. 四个 Agent 的职责与边界

| Agent | 动作 | 产出 | **明确的边界** |
|---|---|---|---|
| collect | `youtube_search` / `youtube_harvest` / `youtube_media` / `x_browser` / `news` / `rebuild_cases` | `metadata/*.jsonl`、`dataset/` | 是**适配器**不是重写：命令原样转给实测过的四个通道脚本，不复制采集逻辑（复制就会有两套行为） |
| cluster | `cluster_cases` | `metadata/events.json` | 只抓**词面近乎同文**的重复；抓不了"同一事件的不同说法"（需实体/时间归一）。合并结果可当**保守下界** |
| verify | `build_packets` | `metadata/verify_packets.json` | **只做证据抽取，不下判断**。三层判定一律"来源归属"（引用核查源原文）；抽不到即 `insufficient`。规则见 [three_layer_judgment.md](three_layer_judgment.md) |
| card | `make_card` | `reports/card_<事件>.md` | 不润色成结论、不补没有的来源；缺口原样印在卡上 |

### verify 为什么不"自己判真假"（重要）

三层判定（事件层／素材层／表述层）是项目里被实战检验的核心方法，它的价值
**恰恰在于不混层**。让脚本直接产出 verdict，等于把三层糊回一层，自毁方法。
所以 verify 只做两件脚本擅长且不会撒谎的事：

- **证据抽取（v2）**：从已落盘的核查源原文里抽评级与关键句，落进三层。
  判定词表是"来源归属"（`source_says_true/false/media_mismatch/mentions/insufficient`），
  每条判定都带 `{case_id, source, field, quote}`，可逐句复核；
- **缺口预检**：`no_authority_source` / `single_platform` / `no_label` /
  `label_conflict` / `many_variants` / `no_media` / `single_domain` /
  `no_source_evidence`；再给检索建议（从**主张标题**剥离话题标签生成，避免
  `fox brand fnc foxnews fact check` 这种没人会去搜的串）。

实测（96 个事件）：**23 个拿到来源证据**（事件层 22、素材层 3、表述层 8），
其余 73 个写 `insufficient` —— 因为语料里只有 30 条核查源案例，
其余是社媒主张本身，没有核查结论可抽。这个数字本身就是"数据不瞄准"的证据。

## 5. 主控职责

| 能力 | 实现 | 实测 |
|---|---|---|
| 队列落盘 | `metadata/tasks.json`（原子写：临时文件 + replace） | 断点续跑可用 |
| 路由校验 | 入队即校验 agent/action 合法性，非法立刻报错 | `未知通道 bogus` 当场拒绝 |
| 依赖编排 | `depends_on`；依赖不全则等待 | 采集 → 组装 → 归簇 → 核查 → 出卡 自动串行 |
| 失败退避 | 30/60/120 秒指数退避（`--backoff` 可调） | 1 次失败 → 1s 后重试 |
| 连续失败封顶 | 默认 3 次 → `paused`，不硬刚 | 2 次失败 → `paused` |
| 阻塞传染 | 上游 paused/blocked → 下游 `blocked`，不假装还有希望 | 已验证（1 paused + 1 blocked） |
| 演练模式 | `run --dry` 走完整条链但不执行、不退避、不落盘副作用 | 整链 5 个任务全走通 |
| 审计 | `metadata/runs.jsonl` 逐次记录 + `logs/orchestrator.log` | 可直接统计成功率 |

## 6. 命令速查

```powershell
$env:PYTHONIOENCODING='utf-8'          # 中文日志不乱码
$py = "fake_news_env\Scripts\python.exe"

& $py prototype\orchestrator.py manifest          # 动作清单（可直接喂 LLM 当 tool schema）
& $py prototype\orchestrator.py plan --channels youtube,x --pipeline --reset
& $py prototype\orchestrator.py run --dry         # 先演练整条链
& $py prototype\orchestrator.py run               # 真跑
& $py prototype\orchestrator.py status            # 看队列

# 只重跑流水线（不采集，对已落盘数据）
& $py prototype\orchestrator.py plan --channels none --pipeline --reset --cards 10
& $py prototype\orchestrator.py run

# 单个 Agent 手工调试
& $py prototype\agents.py cluster cluster_cases --params '{"threshold":0.45}'
& $py prototype\agents.py card make_card --params '{"event_id":"E01"}'
```

`--channels` 可选：`youtube` / `yt-harvest` / `x` / `news` / `none`（不采集）；`full` = youtube,x,news。

## 7. 实测记录（2026-11-03）

### 7.1 整链实跑

| 步骤 | 结果 | 耗时 |
|---|---|---|
| collect `youtube_search`（真实 API，query=deepfake news，limit=5） | `added=5`（535→540 条，配额 100 units） | 5.5s |
| collect `rebuild_cases`（make_cases + 补抓链 + 回归守卫） | 97 → 98 案例；`regressions_restored=0` | ~100s（联网补抓） |
| cluster `cluster_cases`（threshold=0.45） | 98 案例 → **96 事件**，2 次合并 | 0.10s |
| verify `build_packets` | 96 个核查包；**23 个 source_grounded**（事件层 22 / 素材层 3 / 表述层 8）；缺口 high/medium/low = **250 / 98 / 53**；`clean=0` | 0.10s |
| card `make_card`（limit=96） | 96 张事件卡 + 索引；有证据的层带原文引文与原文链接 | 0.1s |

队列终态：`{"done": 3, "paused": 0, "blocked": 0, "total": 3}`。

### 7.2 归簇阈值曲线（写进 events.json，可复现）

| 阈值 | 高于阈值的相似对 | 事件数 | 说明 |
|---|---|---|---|
| 0.20 | 13 | 86（12 次合并） | 误并多（同题材≠同事件） |
| 0.30 | 4 | 94（4 次合并） | 仍含错并（TED 两场演讲、辟谣站两篇不同主题） |
| **0.45** | **2** | **96（2 次合并）** | **默认值：精度优先** |
| 0.50 | 1 | 97（1 次合并） | 漏掉同一事件的不同标题 |

0.45 合并的两对经人工核对成立：
`trump_silences_cnn_reporter_shorts` ≈ `trump_spars_cnn_anchor`（同一场交锋的两条视频）、
两条 `x_deepfake__tweet_*`（同一深伪学生事件）。

**这条曲线本身是结论**：本语料是按关键词采的 98 条**互不相同**的主张，
重复度天然很低（96/98 是单例）。这正好印证了待办里"数据不瞄准"的判断——
要提升归簇产出，得先按核查源主张反向采集，而不是继续调阈值。

### 7.3 失败路径

| 注入 | 观察 |
|---|---|
| `collect/news` 传 `site=bogus` | 第 1 次失败 → 1s 后退避重试 → 第 2 次仍失败 → `paused` |
| 下游任务依赖该失败任务 | 自动转 `blocked`，不再空转 |
| 错误消息可读性 | `exit=2｜news_probe.py: error: argument --site: invalid choice: 'bogus'` |
| 演练模式 | `run --dry` 把 5 个任务的整条链走一遍，不改状态 |

## 8. 已知限制（不粉饰）

1. **主控目前是规则状态机，不是 LLM**。顺序与重试是写死的策略，不会"看着结果
   决定下一步"。升级路径已铺好：`manifest` 的输出就是 tool schema，模型产出
   `{agent, action, params}` 即可复用全部工具（见 §9）。
2. **collect 的真实成功率取决于环境**：YouTube 需 `HTTPS_PROXY` + `YT_API_KEY`
   （本机实测走代理可达）；X 需 Chrome 在线且已登录 x.com；news 采集靠站点模板，
   改版会失效。这些 Agent 只看退出码与落盘增量，判不了"采到的东西质量如何"。
3. **cluster 是词面级**。同事件不同措辞（中英混述、换标题不换画面）会漏。
   要提升必须加实体/时间/地点信号，那是下一版的事，不是调参能解决的。
4. **verify 的证据抽取只在"有核查源"时有效**：96 个事件里 23 个拿到来源证据，
   其余 73 个是社媒主张，没有核查结论可抽，只能给 `insufficient` + 检索建议。
   这不是 bug——社媒一侧是否造假，必须靠外部检索或人工，脚本无权替它定。
   `clean=0` 同理：98 个案例没有任何 L1 标注，每个事件都缺标签。
5. **出卡只到 markdown**。要发 xlsx/docx 还需一步渲染（`office` 技能可接）。

## 9. 下一步（按优先级）

1. **LLM 主控**：`manifest` → 模型决策 → `orchestrator submit`。规则主控保留作兜底
   与回归对照（同一个队列，看两者跑出的产物是否一致）。
2. **verify 接外部检索**：证据抽取（v2）已完成；下一步是把 `search_suggestions`
   喂给检索工具，对 73 个 `insufficient` 事件做外部查证后回填——这是把
   "工作表"变成"核查结论"的唯一合法路径（规则见
   [three_layer_judgment.md](three_layer_judgment.md)）。
3. **cluster 加实体信号**：正则抽人名/地名/日期/数字 → 归一 → 与词面相似度加权，
   专治"同一事件不同说法"。
4. **标注闭环**：verify 的 `no_label` 缺口正是 20 条双人标注试点的入口。

## 10. 新增一个 Agent / 动作（5 步）

1. 在 `agent_protocol.ACTION_MANIFEST` 里登记 `agent.action` + 参数说明
   （这是唯一真源，主控与 LLM 都读它）；
2. 新建 `agent_<名字>.py`，实现 `run(params) -> {status, outputs, artifacts, metrics, error, next}`；
3. 在 `agents.MODULES` 里注册模块名；
4. 若属于采集类（只是转命令），更省事：直接在 `agent_collect.ROUTES` 加一条
   `{script, argmap, flags, watch}`，不用写新文件；
5. 跑 `orchestrator manifest` 自检，再用 `agents.py <agent> <action> --params '{...}'` 手测一次。

## 11. 出卡工具补充（2026-11-04 r12）

子进程方式出卡在 Windows 下偶发"调用失败/静默不写"（杀软扫描抖动，r5/r11/r12 三次踩坑）。
批量出卡或提交前校验请改用进程内脚本：

```powershell
python prototype/refresh_cards.py --events E01,E13,E31 --require-grounded
```

- 进程内 import `agent_card`，不经子进程；出卡后自动校验 mtime/非空，
  `--require-grounded` 额外要求卡内出现「来源称：」；任一失败非零退出；
- `agents.py card` 子进程通道保留（单张手工调试仍可用），批量场景一律走 refresh_cards。
