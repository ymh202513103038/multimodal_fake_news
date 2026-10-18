# 需求文档（试点版 v0.1）

面向**多模态虚假新闻检测**研究，构建「采集 → 数据集制作 → 标注 → 评测」的最小闭环。

- 语言：试点先用英文关键词（`fake news / misinformation / disinformation`）；中文（辟谣平台）放二期；
- 模态：文本 + 图片 + 短视频（暂不做音频字幕提取）；
- 数据源：一期只做 **X(Twitter, twscrape)** + **YouTube(Data API v3 + yt-dlp)**；新闻网站（chrome-mcp）放二期；
- 标签来源：帖子级人工标注为主，事实核查站（Snopes / PolitiFact / 中国互联网联合辟谣平台）做证据对齐；
  标注规范见 [labeling_rules.md](labeling_rules.md)，借鉴 GroundLie360 的「证据式/定位式」标注思想；
- 边界：**仅学术研究，不商用**；只采公开帖文；用户标识脱敏；小批量试采，全程合规
  （见 [compliance_checklist.md](compliance_checklist.md)）。
