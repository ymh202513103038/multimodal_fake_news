# keep_deliver_100 判定准确性审计（v2 复核版，2026-10-22）

> v1 结论（"50%误判"）经复核**撤回**。v1 的错误是我方审计方法缺陷，不是数据集的。
> 本文记录复核过程、证据与最终判定，作为标注试点的校准基线。

## 复核方法

对 v1 判"误标"的 4 行，主动检索**管线所依据的事实核查原文**（含中文核查站），
逐条比对"核查结论 ↔ 表格判断 ↔ 页面内容"三层。

## 复核发现（v1 → v2）

| 行 | v1 判定 | **v2 修订** | 新找到的核查依据 |
|---|---|---|---|
| 2 网易·林肯号 | ✅ | **✅** | [AFP](https://factcheck.afp.com/doc.afp.com.A2LM9CQ)：AI视频+旧视频证伪"击中"；[AGERPRES](https://agerpres.ro/politic-extern/2026/03/06/portavionul-american-uss-abraham-lincoln-nu-a-fost-atins-de-drone-iraniene--1534919)、[GADMO](https://gadmo.eu/nein-diese-videos-zeigen-keinen-iranischen-angriff-auf-die-uss-abraham-lincoln/)同判 |
| 3 维基·11名IRGC | ❌ | **⚠️** | [澎湃明查 2024-01-06](https://roll.sohu.com/a/749901563_260616)：空袭真实但无伤亡报告；"11人死亡"唯一信源为沙特Al-Hadath匿名信源，IRGC发言人否认"毫无根据"→说法实质被证伪；但维基同页记录否认，判"传播"略严 |
| 6 IG·Xbox激光 | ❌ | **✅** | [澎湃明查 2026-10-04](https://nw.eastday.com/zq/zh/20261004/727875fc28013870cc140d7c0fa91f28.html)："用Xbox手柄控制激光武器摧毁伊朗基础设施？**误导信息，还做不到**"——项目存在（NY Post）≠已在实战拦截；表格判对 |
| 15 IG·默克莱 | ❌ | **✅** | [AFP](https://factcheck.afp.com/doc.afp.com.C62H9ZQ)：亲政府帖文循环旧战争画面；[AFP Factuel](https://factuel.afp.com/doc.afp.com.D2EE8CR)：该图并非2026年10月默克莱埃塞士兵——事件真（AP）但画面假（AFP） |
| 18 YT·内塔尼亚胡 | ✅ | **✅** | [RTVE](https://amp.rtve.es/noticias/20260320/video-ultrafalso-bulo-falsa-muerte-netanyahu/16988774.shtml)：Hive/Hiya判定AI生成；内塔尼亚胡3/15喝咖啡、3/19记者会（AP/Reuters在场） |
| 23 IG·RAF | ⚠️ | **✅** | 保存页完整图注："Cockpit Footage of RAF Eurofighter Typhoon **Destroying Iranian UAV Over Jordan**"+关键词含 interception/missile strike——帖子自身宣称战果，发布于开战第18天左右（v1/v2.0误算为"31周前=战前5个月"且截断了图注）；[factcheckcyprus.org](https://factcheckcyprus.org/tag/raf/) 有RAF专项核查 |
| 48 FB·No Kings | ❌ | **⚠️** | 事件层为真：[CBC 2026-03-27](https://www.cbc.ca/lite/story/9.7144237)证实900万为主办方Indivisible预估、伊朗战争为议题之一；素材层确有错配：[RTL](https://today.rtl.lu/news/fact-check/video-shows-trump-supporters-storming-us-capitol-not-rally-against-middle-east-war-6310016)（国会山视频冒充反战集会）、[希腊核查站](https://www.factchecker.gr/tag/no-kings/)。deliver_why写在事件层（错），核查实际在素材层（对）——两层错位 |
| 92 早报·使馆 | ⚠️ | **⚠️** | 使馆遇袭为真实战况（早报当日报道）；核查针对旧视频素材；早报未使用该视频，归入名单口径过宽 |

**修订后统计：✅ 5 ｜ ⚠️ 3 ｜ ❌ 0 —— 原表 8 条无硬错。**

> v2.1 勘误（2026-10-22）：行23 RAF 的 v2 判定依据有两处错误——①"31周前"换算错误
> （应为2026年3月中旬、开战期间，而非2025年10月）；②图注提取时截断，漏掉了
> "Destroying Iranian UAV Over Jordan"的关键宣称。修正后该行改判 ✅（页面自身
> 宣称战果，属传播节点）。此勘误由用户质询触发——**人工复核必须输出完整图注与
> 准确时间换算，不能依赖截断摘要**。

## v1 审计的三个方法错误（沉淀为标注 checklist）

1. **检索语言偏向**：只搜英文核查（AFP/RTVE/GADMO），漏掉中文核查站（澎湃明查/
   搜狐明查）——本管线核查源以中文为主（deliver_why 全中文即证据）。
   → 教训：核查检索必须覆盖主张的原始语言圈；
2. **事件层/素材层混淆**：默克莱事件真（AP）≠画面真（AFP证伪）；Xbox 项目真
  （NYP）≠实战使用真（澎湃证伪）。→ 教训：判定必须分三层——底层事件 /
   传播素材 / 表述口径；
3. **单一信源当多源**："11名IRGC"的 adevarul/ICIBeyrouth 全部溯源到 Al-Hadath
   一个匿名信源（IRGC 否认）。→ 教训：数"源头数"不数"报道家数"。

## 数据集质量最终评价（修正版）

1. **判定层可靠**：抽检 8 条无硬错，4 对 4 部分；LLM 判定在真谣言上准确，
   在边界样本上口径偏严但可辩护；
2. **核查源为中文核查站**（澎湃明查体系），"中文主张+多语言扩散追踪"定位自洽；
3. **真实短板在工程层（维持 v1 结论）**：
   - 缺 `factcheck_url` 列——判定无法溯源（本次复核走弯路的直接原因）；
   - deliver_why 粒度粗——未写明核查针对的层级（事件/素材/表述）；
   - keep 口径未文档化——"传谣页"还是"扩散链全景页"（含背景/报道）定义不清；
4. **外推警告（维持）**：8 条为定向抽检，非随机样本；全量质量仍需 10% 随机
   人工复核验证。

## 交付物

- 本文档（v2）；
- 8 行人工标注（待写入 keep_deliver_100.xlsx 新列：人工判定 + 判定理由）。
