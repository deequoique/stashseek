# 技术设计

检索采用向量召回、BM25/OCR/ASR、结构化过滤和结果去重的混合排序。媒体候选与文本 Segment 统一为可引用 Hit，但保留媒体类型和时间定位。回填按 tenant/content item 分批执行，灰度只开启新索引读取，不改变旧文本写入契约。

## 三路合并（2026-10-15 用户决定）

### 召回

每次检索并行执行三路，各自取候选池：

| 路 | 查询编码 | 检索对象 | 现有实现 |
|---|---|---|---|
| ① BM25 | 无（`similarity` / `ts_rank_cd`） | 文本 Segment（含 ASR 文本） | `app/retrieval/search.py::bm25_search` |
| ② 文本向量 | 智谱 embedding-3，1536 维 | `Segment.embedding` | `app/retrieval/search.py::vector_search` |
| ③ WeMM 向量 | WeMM-2B，1024 维 | 媒体资产向量（图片 / 视频片段） | `app/retrieval/media.py::MediaRetrievalService`（尚无真实 repository） |

### 融合

- 三路分数尺度不同（trigram/ts_rank、两个不同模型空间的 cosine），不能按原始分数直接排序；改为按名次融合（建议 RRF，k 值及各路权重由 benchmark 决定）。
- 现状备注：当前 `KnowledgeServices.search` 把 ①② 的命中拼在一起后由 `_diversify_hits` 按原始 `hit.score` 排序，本身就存在尺度不一致；三路合并实现时一并改为名次融合，并用 meeting_gold 回归确认文本质量不下降。
- 融合单位：文本命中是 Segment，媒体命中是带时间范围的媒体片段。按视频（`item_id`）分组沿用现有 `_diversify_hits` 的“每个视频先保留一个代表”规则；同一视频内时间重叠的 Segment 与媒体片段的去重/合并规则待定。
- 返回结果区分证据类型：Segment 保留 `[S...]`；媒体片段保留 `media_id`、时间范围，仍需 `inspect_media` 核验后才能成为回答证据。

### 降级与成本

- WeMM 查询编码发生在用户提问时，三路合并意味着每次检索都要调用一次 WeMM；上线需要常驻、低延迟的 WeMM 服务，按需冷启动（数十秒）不适合用户请求路径。
- WeMM 超时或不可用时退回 ①② 两路，并记录降级事件；不得让媒体路失败导致整次检索失败。
- feature flag 关闭时只跑 ①②，与现有行为一致。

### Agent 侧

- 主 Agent 只调用一个检索工具，不再自己判断走不走 WeMM；`search_media` 作为独立工具的形态将被合并进统一检索。
- 检索预算（`NORMAL_SEARCH_CALLS_LIMIT = 2`）按“一次三路检索 = 一次调用”计。
