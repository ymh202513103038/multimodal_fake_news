# 事件卡 E02｜Trump tells ‘FAKE NEWS’ CNN reporter to be…

> 由真途（Zhentu）多智能体流水线自动生成 · 2026-11-04T13:41:08+00:00 · 簇内 2 条案例 / 1 个平台

## 1. 事实基座（簇内成员，全部可点开复核）

| # | 平台 | 主张（标题） | 案例 ID | 来源 |
|---|------|--------------|---------|------|
| 1 | youtube | Trump tells ‘FAKE NEWS’ CNN reporter to be quiet #shorts #foxnews #news #fox | `us_politics__trump_silences_cnn_reporter_shorts` | [打开](https://www.youtube.com/watch?v=sc9uFvYpm7Y) |
| 2 | youtube | Trump goes after 'fake news,' spars with 'low-rated' CNN anchor | `us_politics__trump_spars_cnn_anchor` | [打开](https://www.youtube.com/watch?v=UzH7eLdimwE) |

- 平台分布：`{"youtube": 2}`
- 表述变体：2 种（同一事件的不同说法，需逐变体判表述层）
- 簇内高频词（聚类依据，非结论）：fox、brand、fnc、foxnews、cnn、cable、trump、channel

## 2. 三层判定（事件 / 素材 / 表述）

> 三层必须分开判。混层是这个领域最常见的误判来源：事件真 ≠ 画面真 ≠ 表述不夸大。
> 下表的判定**全部归属核查源原文**：「来源称」= 引用核查站的说法，不是本卡自己的结论；「证据不足」= 抽不到可引用原文，需外部检索。

| 层 | 要回答的问题 | 来源判定 | 依据（原文摘录 · 案例） |
|----|--------------|----------|--------------------------|
| 事件层 | 这件事本身发生了吗？有无独立信源（对方阵营／第三方核查／官方通报）？ | **来源称：成立** | Fox News halted for breaking alert as Donald Trump completely loses it at reporter——Fox 直播因 Trump 在 Oval… · `external:themirror.com` |
| 素材层 | 配图／视频／音频是否属于该事件？旧素材、AI 生成、跨事件错配都在这一层判。 | **证据不足** | — |
| 表述层 | 说法是否超出证据？'彻底摧毁／全部投降／无一幸存'这类强断言有无支撑？ | **证据不足** | — |

- L1 二分类：**未定**（无核查源评级可映射，需外部检索或人工标注）

### 来源原文摘录（可逐句复核）

- **事件层**（external:themirror.com / external_retrieval · `external:themirror.com` · [原文](https://www.themirror.com/entertainment/donald-trump-fox-news-cnn-1988041)）：
  > Fox News halted for breaking alert as Donald Trump completely loses it at reporter——Fox 直播因 Trump 在 Oval Office 对 CNN 记者动怒插播中断；另见 HuffPost：Trump Throws 'Dummy' Tantrum Over Kaitlan Collins' Uncomfortable Question

## 3. 证据缺口（这张卡的边界所在）

- 🔴 **no_authority_source**（high）：簇内没有任何核查机构/通讯社/官方来源，事件层无法内证，必须外查
- 🟠 **single_platform**（medium）：只在一个平台出现，跨阵营互证不成立
- 🔴 **no_label**（high）：簇内没有任何标签，需先完成 L1 二分类
- 🟠 **single_domain**（medium）：簇内全部来自同一域名 youtube.com，是同一源头被搬运转述
- 🔴 **no_source_evidence**（high）：三层都抽不到来源证据：需外部检索（见 search_suggestions）

## 4. 下一步核查建议

建议检索式（按主张原始语言圈检索——中文主张去中文核查站，英文主张去英文核查站）：

- `Trump tells FAKE NEWS CNN reporter to be quiet fact check`
- `Trump tells FAKE NEWS CNN reporter to be quiet 辟谣`
- `Trump tells FAKE NEWS CNN reporter to be quiet debunk`

预检事实（确定性统计，可直接引用）：

| 指标 | 值 |
|------|-----|
| 平台数 | 1 |
| 表述变体数 | 2 |
| 权威来源域名数 | 0 |
| 独立域名数 | 1 |

---

*本卡由流水线生成。判定口径为「来源归属」：每条判定都能回到上方引用的原文；「证据不足」代表抽不到可引用原文，**不代表主张为真**，需按 `documentation/three_layer_judgment.md` 做外部检索后回填。*
