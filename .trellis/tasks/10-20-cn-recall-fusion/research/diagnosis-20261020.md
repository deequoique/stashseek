# 诊断记录：「电脑相关的视频」为什么没召回两条 B 站视频（2026-10-20）

所有数据来自生产库只读查询和生产日志，租户 `app_user_id = 211`（lane 的网页账号，16 个 ready 条目）。复现脚本在同目录：`replicate_pool_tenant211.py`（复制 `search_segments` 的候选池逻辑）、`leaderboard_tenant211.py`（按视频的最佳余弦排行、重复片段、B 站片段预览）。运行方式：

```bash
.venv/bin/python .trellis/tasks/10-20-cn-recall-fusion/research/replicate_pool_tenant211.py
.venv/bin/python -m app.cli search "电脑相关的视频" --user-id 211 -k 50
```

## 1. 日志里的 trace

生产日志 `/var/log/notebook-agent/notebook-agent-2026-10-19.log`，request `279f415154974956ad8a7ebc0ce2bc70`，11:22:40 UTC：

| 阶段 | 数据 |
|---|---|
| route | agent |
| embedding_started → embedding_completed | 1.06 s |
| retrieval_started → retrieval_completed | 4.26 s |
| tool_call search_segments succeeded | result_count=10，call_index=1（只搜了一次） |
| stage_usage | request_count=2，tool_call_count=1 |
| citation_validated（answer 阶段） | result_count=8 |
| 整轮 | 18.5 s（journald `conversation_stream_lifecycle`） |

生产日志不含查询文本、片段 ID 和分数（`logging-guidelines.md`）；`conversation_turn.sources` 只存 Composer 选中的 8 条。

## 2. 候选池复现

`search_segments` = `bm25_search(k=50)` + `vector_search(k=50)` → `_diversify_hits(limit=10)`。

**查询「电脑」**（词法 2 条，向量 50 条）→ 10 条候选的片段 ID：925、5945、1691、5102、2962、1725、6833、1105、1737、1437。答案引用的 8 个 ID（1691、1725、1737、1105、925、5945、5102、2962）全部在其中，可以确定 Agent 实际用的查询是「电脑」。5 个视频名额被 154、225、156、224、222 占满，代表片段分别是 "An"、"And"、"Computer only 100 and one."、"Hey."、"A?"。词法命中的两条来自 item 429，分数 0.0089 和 0.0053，在混排里垫底。

**查询「电脑相关的视频」**（词法 0 条，向量 50 条）→ 视频顺序 156、222、224、225、429（第 16 名，0.5032）；429 作为第 5 个视频进池，带一条片段。430 不在前 50。

**查询「电脑 内存 显卡 价格」** → 429 四条片段全部进池（0.67 到 0.68），430 第 4 个视频（0.549）。

**查询「什么时候买内存」** → 429 前 4 名（0.60），430 第 9 名（0.53）。

## 3. 按视频的最佳余弦排行（全部 ready 片段）

查询「电脑相关的视频」：

| 名次 | item | 最佳余弦 | 片段数 |
|---|---|---|---|
| 1 | 156 Week 1 - Lecture 1 (Wed) | 0.5793 | 1027 |
| 2 | 222 week2_lect1_thurs | 0.5511 | 1313 |
| 3 | 224 week3_lect1_wed | 0.5455 | 1100 |
| 4 | 225 week3_lect2_sat | 0.5396 | 1572 |
| 5 | 429 等等党/电脑价格 | 0.5032 | 4 |
| 6 | 29 Why Europe … air conditioning | 0.4875 | 58 |
| 15 | 430 什么时候买内存 | 0.3454 | 18 |

查询「电脑」：429 第 8（0.4990），430 第 13（0.3487）。

## 4. 条目与片段

| item | 平台 | lang | 来源 | 片段数 | 平均字符 | 说明 |
|---|---|---|---|---|---|---|
| 156 / 222 / 224 / 225 | ntu_kaltura | en | official_cc | 1027 / 1313 / 1100 / 1572 | 67–71 | 2026-08-17/24 入库，切块修复前的碎片 |
| 154 / 155 / 157 | ntu_kaltura | en | official_cc | 224 / 139 / 238 | 571–688 | 同期入库，正常 |
| 429 | bilibili | zh | auto_caption | 4 | 293 | 正文含"电脑"的片段 2 条 |
| 430 | bilibili | en | auto_caption | 18 | 783 | 英文字幕，讲 DRAM/chips，正文无"电脑"/"computer" |

重复片段：`'Okay.'` ×20（4 个条目）、`'All right.'` ×18、`'Okay?'` ×16、`'Right?'` ×13；共 108 个片段属于重复组。

用当前切块器重跑 156 的原始字幕（2305 条 cue，平均 30 字符）：113 块，平均 729 字符，中位 140 词，无 40 字符以下的块。154 同样：181 块，平均 689 字符。结论：碎片是旧数据，新入库不会再碎。

## 5. 中文词法路径的数值

`pg_trgm.similarity_threshold = 0.3`。`similarity(片段, '电脑相关的视频')` 在租户所有中文片段上的最大值远低于阈值（短查询对长片段的理论上限约 0.02），所以 `%` 从不触发；只有 `ILIKE '%电脑%'` 这种整串子串能命中（429 的 2 条）。

## 6. 生产规模（2026-10-20）

- ready 条目 131，片段 63,520，其中中文条目片段 149，有简介的条目 28。
- Neon 可用扩展：pg_trgm、vector、rum。无 zhparser / pg_jieba / pg_bigm。
- `segment` 现有索引：主键、`(item_id, seq)` 唯一、`fts` GIN、`text` trigram GIN。
