# 技术设计：标题入索引、RRF 融合、中文词法

需求见 `prd.md`，执行计划见 `implement.md`。本文定边界、契约、数据流、取舍和回滚。

## 1. 边界

改动集中在四层，层与层之间只通过现有类型传递：

| 层 | 文件 | 改什么 |
|---|---|---|
| 检索后端 | `app/retrieval/search.py`、新增 `app/retrieval/fusion.py`、新增 `app/retrieval/zh_tokens.py` | 中文词法改分词全文检索；融合与候选窗口独立成模块；`Hit` 带 `boundary_kind` |
| 知识服务 | `app/agent/services.py` | `search_segments` 改为"多路取候选 → RRF → 窗口 → 水合"；标题行的引用投影；`get_neighbors`/`open_at` 排除标题行 |
| 入库 | `app/ingest/tasks.py`、`app/cli.py` | 入库时写中文 `fts` 和标题行；运维命令补齐存量 |
| 答案层 | `app/agent/answer_pipeline.py`、`app/agent/types.py` | `Citation.kind`；Composer 和来源区块识别标题行 |

不动的：`Citation` 已有字段的含义、SSE 事件、MCP 返回结构、`vector_search` 的 filter-first 排序、英文词法路径、Agent 的检索指令、工具签名和预算、日志契约、数据库表结构（不需要迁移）。

## 2. 数据流

```text
用户问题 ──► Turn Agent ──► search_segments(query)
                               │
                               ├─ 向量化：embedder.embed([query])                  （一次 HTTP，和现在一样）
                               ├─ lexical(query, k=pool)、vector(query, k=pool)    （两条 SQL，和现在一样）
                               ├─ fuse_rrf([lexical, vector], k, weights)  → 按 segment_id 去重、名次相加
                               ├─ select_window(fused, limit=10, per_item_cap=3)
                               └─ 水合（租户/状态条件不变） ──► Citation 列表（标题行 → kind="title"）
```

## 3. 契约

### 3.1 融合模块 `app/retrieval/fusion.py`

```python
@dataclass(frozen=True)
class FusionConfig:
    method: Literal["rrf", "raw"] = "rrf"
    rrf_k: int = 60
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    candidate_pool_limit: int = 50
    per_item_cap: int = 3

def fuse_rrf(lists: Sequence[Sequence[Hit]], *, k: int, weights: Sequence[float]) -> list[Hit]
def fuse_raw(lists: Sequence[Sequence[Hit]]) -> list[Hit]          # 现状：同一片段取最高原始分
def select_window(fused: Sequence[Hit], *, limit: int, per_item_cap: int) -> list[Hit]
```

- `fuse_rrf`：每个列表先按原始分数降序去重（同一片段取最高分），第 r 名贡献 `w / (k + r)`；同一片段在多个列表中得分相加；输出按 `(score desc, segment_id asc)` 排序，`Hit.score` 改为融合分。列表为空时跳过，不影响其他路。`lists` 的长度不限，WeMM 第三路只是多传一个列表和权重。
- `select_window`：两段式。第一段按融合名次遍历，每个视频最多 `per_item_cap` 条，到 `limit` 为止；第一段不足 `limit` 时，第二段按融合名次把剩余命中补满到 `limit`，不再受上限约束（第一段在前）。上限只用来保证多样性，窗口不会因此变短；限定单个视频的检索会自然补满，等价于纯名次截断。`per_item_cap >= limit` 等价于不限制。（2026-10-20 RQ1 发现单段式让范围 A 的单视频检索每题只剩 3 条、Recall@5 从 0.381 掉到 0.256，故改为两段式。）
- `fuse_raw` + 旧的 `_diversify_hits` 保留，`method="raw"` 时走旧路径，供回滚和评测基线。

配置（`app/config.py`，`Settings.__post_init__` 校验）：

| 环境变量 | 默认 | 范围 |
|---|---|---|
| `RETRIEVAL_FUSION` | `rrf` | `rrf` / `raw` |
| `RETRIEVAL_RRF_K` | 60 | 1–1000 |
| `RETRIEVAL_LEXICAL_WEIGHT` / `RETRIEVAL_VECTOR_WEIGHT` | 1.0 | 0–10，不能同时为 0 |
| `RETRIEVAL_CANDIDATE_POOL_LIMIT` | 50 | 10–200 |
| `RETRIEVAL_PER_ITEM_CAP` | 3 | 1–10 |

`app/bootstrap.py` 从 `Settings` 构造 `FusionConfig` 传给 `KnowledgeServices(..., fusion=...)`；不传时用默认值，现有测试不受影响。

### 3.2 中文分词 `app/retrieval/zh_tokens.py`

```python
def cjk_tokens(text: str) -> list[str]
def zh_tsvector(text: str)   # -> func.to_tsvector("simple", " ".join(cjk_tokens(text)))
def zh_tsquery(query: str)   # -> func.websearch_to_tsquery("simple", " OR ".join(cjk_tokens(query)))
```

- 文本按字符类切成"CJK 连续段"和"ASCII 词"。CJK 段取相邻双字（`电脑价格` → `电脑 脑价 价格`），长度 1 的段取单字；ASCII 词小写保留（`DDR5`、`5070`）。
- 跳过包含语气助词的双字（`的 了 吗 呢 吧 啊 呀`），避免 `的视` 这类噪声；去重；最多 64 个词元。
- 查询侧用 `websearch_to_tsquery` 的 `OR` 语法，不手拼 `to_tsquery`，避免转义问题。
- `bm25_search` 中文分支改为：`Segment.fts @@ zh_tsquery(q)`，按 `ts_rank_cd(Segment.fts, tsquery)` 降序；去掉 `lang LIKE 'zh%'` 条件（英文条目的 `fts` 不含 CJK 词元，自然不命中；混合查询里的 ASCII 词可以命中）。不再用 `%`、`similarity`、ILIKE。
- 入库（`tasks.py:577`）：`lang` 以 `zh` 开头时 `fts = zh_tsvector(part.text)`；其他不变。
- 英文分支（`websearch_to_tsquery('english')`，AND 语义）不变。

### 3.3 标题行

| 字段 | 值 |
|---|---|
| `seq` | `-1`（`(item_id, seq)` 唯一约束保证每条目一行） |
| `start_sec` / `end_sec` | `0`（满足 `loc_ck`，不用于显示） |
| `text` | `标题\n简介前 400 字符`；没有简介时只有标题 |
| `embedding` | 对 `text` 的向量 |
| `fts` | 按条目语言：中文走 `zh_tsvector`；其它语言用 `to_tsvector('english')`，但标题文本含 CJK 时（中文标题配英文字幕的转载视频）再拼接 `zh_tsvector`（`english || simple`），让中文查询在词法路也能命中标题 |
| `boundary_kind` | `"title"` |

- `Hit` 增加 `boundary_kind: str = ""`；`_hits` 从 `Segment.boundary_kind` 填充。
- `Citation` 增加 `kind: Literal["segment", "title"] = "segment"`（有默认值，旧客户端忽略）。`_citation` 对标题行：`kind="title"`、`start_sec=None`、`url=item.url`、`excerpt=text`。
- Composer（`_render_composer_evidence`）对 `kind == "title"` 渲染为 `ID N，视频《T》的标题与简介：…`；来源区块（`_append_sources`）渲染为 `[S N] url — 标题与简介：…`。聊天界面 `start_sec` 为空时已显示"打开 ↗"，不改。
- 排除：`get_neighbors` 的锚点和邻居查询、`open_at` 都加 `Segment.boundary_kind != "title"`；评测的片段到 cue 映射跳过标题行。`app/agent/context.py` 的来源可用性检查不需要改（只验证归属）。
- 入库：`process_item` 在生成正文片段向量的同一批里加入标题文本（`semantic([title_text, *chunk_texts])`），删除旧片段后一起插入。标题文本计入现有的向量化预算。
- 不生成的情况：条目不是 ready、已删除、已归档。条目删除时标题行随 `segment` 级联删除。

### 3.4 运维命令 `python -m app.cli reindex`

```text
python -m app.cli reindex [--user-id N] [--dry-run] [--zh-fts] [--title-rows]
```

- `--zh-fts`：对 `lang LIKE 'zh%'` 且 `fts IS NULL` 的片段补写 `fts`；纯 SQL 更新，不调向量 API。
- `--title-rows`：对 ready 且未删除、未归档的条目，缺标题行或标题行文本与当前标题简介不一致的，向量化后插入或更新。按条目提交。
- 两个开关都不给时两项都做。输出只有数量和条目 ID，不输出标题和文本。干跑只统计。
- 不放在 Alembic 迁移里：迁移单元没有向量 API 的网络访问，且 131 次向量化不应阻塞部署。

### 3.5 评测接入

- `evals/meeting_gold/retrieval_study.py` 改为从 `app/retrieval/fusion.py` 导入 `fuse_rrf`/`fuse_raw`，并给 `rank_setting` 增加 `per_item_cap` 选项，新增设置 `rrf/AND/cap3`，作为"新线上做法"。原 `raw/AND/div-on` 仍是旧线上基线。
- 租户 211 的中文对比用 `research/replicate_pool_tenant211.py` 改造：直接调用 `KnowledgeServices.search_segments`，分别在 `RETRIEVAL_FUSION=raw` 和 `rrf` 下输出候选池。

## 4. 取舍

- **双字 n-gram 而不是分词器**：Neon 没有 zhparser/pg_jieba，jieba 是新依赖且词典加载慢。双字对未登录词鲁棒，代价是词元数多、噪声多，靠 `ts_rank_cd` 和 RRF 压制。生产中文片段只有 149 个，存量成本可忽略。
- **OR 语义只用于中文**：英文保持 AND，RQ1 测试集显示 OR 在全库检索有害。中文双字必须 OR，否则多字查询几乎不可能全部命中。
- **标题行做成片段而不是条目级索引**：复用全部现有路径（两路检索、融合、水合、引用、来源渲染、租户条件），只多一个 `boundary_kind` 判断；代价是要在枚举正文的地方排除它，现查到的只有 `get_neighbors`、`open_at` 和评测映射。
- **按名次融合后的分数不再有物理含义**：`Citation._retrieval_score` 和 `UnifiedRetrievalCandidate.score` 变成 RRF 分（约 0.01–0.04）。现有消费方只做排序和开发日志，不比较阈值。
- **每视频上限 3 而不是关闭 diversify**：RQ1 显示关闭最好，但产品要求候选能看出多个视频；上限 3 在 10 条里至少容纳 4 个视频，且可配置为 10 等价关闭。
- **不改 Agent 指令、不做原问题兜底**（用户 2026-10-20 决定）：检索要对短查询本身鲁棒，每次查询的向量化和 SQL 次数保持不变；用「电脑」这个短查询做验收。代价是正文和标题都不含查询词的视频（如 430 对「电脑」）只能靠向量相似度，不保证命中。

## 5. 兼容与回滚

- 无表结构变更，无迁移。标题行只是 `boundary_kind='title'`、`seq=-1` 的普通行。
- `RETRIEVAL_FUSION=raw` 立即恢复旧的混排和 5 视频窗口（标题行仍参与，若也要去掉：`DELETE FROM segment WHERE boundary_kind = 'title'`）。
- 中文 `fts` 补写是单向的，但旧代码不读它（旧中文路径不看 `fts`），回滚代码后无副作用。
- `Citation.kind` 有默认值；旧插件和 MCP 客户端忽略未知字段。
- 部署顺序：合并到 main → 部署 → 在生产主机运行 `stashseek reindex --dry-run` 看数量 → 运行 `python -m app.cli reindex`。在此之前新查询已经按 RRF 工作，只是存量中文片段的词法和标题行还没生效。

## 6. 风险

- 英文课程问答的排名变化：RQ1 范围 A 回归是门槛（见 `prd.md` R5）。
- 标题行被 Composer 过度引用（答案变成"视频 X 的标题提到…"）：Composer 指令里说明标题与简介只用于判断哪个视频相关，具体事实仍以正文片段为准；评测看引用分布。
- `websearch_to_tsquery('simple', …)` 对 CJK 的切分依赖 Postgres 默认解析器把 CJK 字符当作词字符；入库和查询用同一个函数生成词元并用空格分隔，解析器只需要按空格切分。实现时先在测试库验证 `to_tsvector('simple', '电脑 脑价')` 得到两个词元。
