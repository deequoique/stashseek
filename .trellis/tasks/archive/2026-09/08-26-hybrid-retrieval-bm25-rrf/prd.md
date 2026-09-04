# 升级混合召回：标准 BM25、RRF 与检索评测

## Goal

把当前名为 `bm25_search`、实为 PostgreSQL `ts_rank_cd` / 中文 trigram 的
lexical 路径升级为可准确命名、可独立评测、可安全回滚的混合召回：生产 Neon
可使用标准 BM25，普通 PostgreSQL 保留能力明确的全文检索 fallback，所有 lexical
与 pgvector 候选通过 rank-based fusion 合并，不再直接比较不同后端的原始分数。

## Requirements

### R1 — 先建立可复现的检索评测基线

- 在现有 `evals/` 体系中增加不依赖 Agent/Composer 判断的 retrieval benchmark，直接
  比较 lexical、vector 和 fused 三条排名。
- 数据集必须覆盖专有名词/人名、缩写、错误码或代码符号、英文精确短语、中文精确
  短语、中文问题查询英文内容、语义改写、跨片段以及 no-evidence。
- 复用已有 Gold Evidence schema、fixture 和隐私边界；允许补充新的固定公开 fixture，
  但不能用真实用户数据、某次运行才成立的自增 ID 或模型生成标签充当 gold。
- 至少报告 Recall@1、Recall@3、Recall@10、MRR、no-evidence false-positive rate 和
  distinct-item coverage；分别展示 lexical、vector、fusion，不把最终 Citation 选择
  当作检索命中。

### R2 — 准确区分 lexical fallback 与标准 BM25

- 将会误导维护者的 `bm25_search` 命名改为与真实实现一致的接口名；PostgreSQL
  `ts_rank_cd` 和 `pg_trgm` 不得继续在代码、CLI 或文档中宣称为标准 BM25。
- 定义一个窄的 lexical backend contract，使 PostgreSQL FTS fallback 与标准 BM25
  返回相同的 tenant-scoped ranked-hit 结构，但保留 backend/rank 信息供融合和评测。
- Neon 目标后端使用官方 `lakebase_text` 的 `lakebase_bm25` 索引；不得为新项目采用
  已弃用的 `pg_search`。
- BM25 扩展不可用、索引缺失或查询失败时必须按显式配置处理：要求 BM25 的部署应在
  readiness 阶段失败闭合；明确选择 PostgreSQL fallback 的部署继续使用 FTS。运行中
  不得静默从 BM25 降级并把结果伪装成同一后端。

### R3 — 中英文与 code-switch lexical 文档

- 英文继续使用 PostgreSQL 英文 text-search configuration 的 stemming/normalization。
- 中文不能继续依赖 `english` parser，也不能只因 query 含 CJK 就排除英文 item。
  设计并测试确定性的中文 lexical tokenization；segment 和 query 必须使用同一规则。
- code-switch、产品名、大小写、连字符、版本号和常见代码/错误标识不得被中文路径
  丢弃。中文分词实现必须有纯函数 fixture 测试，不依赖在线服务。
- 已有 ready segments 必须有可恢复、可验证的 backfill/reindex 路径；新摄入和重试
  必须产生相同 lexical 文档。

### R4 — 使用 RRF 融合，不比较跨后端原始分数

- lexical 与 vector 各自保持内部相关性排序；融合只使用 backend rank 和 segment ID。
- 同一 segment 被两路命中时累计两路贡献；单路命中仍可进入候选集。RRF 后再执行
  现有 item-level diversification，继续满足最多 5 个 items、公开最多 10 个 segments、
  每后端候选池最多 50 的有界约束。
- RRF 常数和可选权重必须通过固定 benchmark 选择并写入设计/测试；不得只因论文默认
  值而采用 `k=60`，也不得在生产请求中接受模型或用户传入的 ranking 参数。
- Citation hydration、顺序和 `_retrieval_score` 必须使用清晰定义的 fused score 或
  rank；不得再取不同量纲原始分数的 `max()`。

### R5 — 保持现有安全、可靠性和 Agent 边界

- 每条 lexical/BM25、vector、fusion 后 hydration 均继续重复 tenant、active、
  `deleted_at IS NULL`、`archived_at IS NULL`、ready、可选 item/reference scope predicates。
- query embedding 失败仍返回 `embedding_unavailable`；不得把 lexical-only 结果作为
  hybrid 成功。数据库/BM25 查询失败仍返回 `retrieval_unavailable`，零命中仍是
  `not_found/no_evidence`。
- 不修改 5/2/3 retrieval convergence budget、Agent tool schema、Composer Citation
  allow-list、最多五个来源和最多八个最终证据的约束。
- 生产诊断只允许 backend 名、候选数、耗时、固定错误码和数值排名指标；不得记录
  query、segment 文本、URL、向量、BM25 term 或用户资料。

### R6 — 迁移、兼容与回滚

- Alembic 保持单 head；扩展启用、列/backfill、索引建立和应用切换必须分阶段，避免在
  一个不可恢复事务中同时改变 schema、全量数据和运行时行为。
- production migration 仍只使用 direct Neon URL；长运行服务仍只使用 pooled URL。
- 发布前必须在隔离 Neon branch 验证 `lakebase_text`、BM25 index、tenant filters、
  `EXPLAIN` 和真实 top-K；不得在共享 production 数据库上做破坏性试验。
- 普通 PostgreSQL/local profile 必须有明确支持矩阵。若保留 FTS fallback，其配置、
  指标标签和效果差异必须可见；若某 profile 要求 BM25，则 readiness 必须拒绝缺扩展。
- 回滚必须能先切回旧 lexical backend/fusion mode，再独立处理新索引；不得要求删除
  segment、embedding 或用户内容。

## Acceptance Criteria

- [ ] Retrieval benchmark 数据集通过严格校验，并覆盖 R1 的九类问题；合成排名测试
      能精确验证 Recall@K、MRR、false-positive 和 distinct-item coverage。
- [ ] 在同一固定语料上生成 lexical、vector、fusion 的基线报告；报告包含样本分母、
      skip/unscorable 原因和配置版本，不含 query/正文/URL/用户标识。
- [ ] 代码、CLI 和文档不再把 `ts_rank_cd`/trigram 称为标准 BM25；backend 名称与
      实际算法一致。
- [ ] RRF 单元测试覆盖双路重复、单路命中、分数尺度极端差异、稳定 tie-break、候选池
      边界、单视频 crowding 和六视频竞争；结果不依赖 raw score 的数值范围。
- [ ] 中文、英文和 code-switch tokenization 测试覆盖人名、产品名、连字符、版本号、
      CJK 短词与无空格字幕；新摄入与 backfill 生成一致 lexical 文档。
- [ ] 隔离 Neon branch 上启用 `lakebase_text`，建立 `lakebase_bm25` index，并证明
      BM25 查询在 tenant/item/reference predicates 下只返回授权的 active ready rows。
- [ ] 要求 BM25 但缺扩展/索引的 profile 在 readiness 阶段安全失败；显式 FTS fallback
      profile 可启动且报告真实 backend，不发生静默降级。
- [ ] 旧数据 backfill/reindex 可重复、可断点恢复且不改变 segment ID、embedding、
      timestamp 或内容；迁移后 Alembic 仍为单 head。
- [ ] 固定 benchmark 上 fusion 的 Recall@3 和 MRR 不低于现有 hybrid baseline，
      no-evidence false-positive rate 不上升；至少一个 lexical-oriented 子集
      （专有名词/精确短语/代码标识）相对 vector-only 有可复现提升。若不满足，不切换默认。
- [ ] 现有 Agent retrieval、exact/item scope、删除/归档、multi-user isolation、diagnostics、
      Citation/Composer、自然语言评测和完整测试套件不回归。
- [ ] 配置、运维、摄入/召回说明和 backend 支持矩阵更新；发布步骤包含 benchmark、
      Neon branch migration、readiness、切换与回滚命令。

## Out of Scope

- 本任务不引入 Elasticsearch/OpenSearch、外部托管搜索服务或 LLM reranker。
- 不更换 embedding provider、向量维度、HNSW 参数、chunking 策略或 Agent 模型。
- 不让模型选择 lexical backend、RRF 参数、tenant、item ownership 或结果上限。
- 不用未经评测的 cross-encoder 作为发布门槛；如 benchmark 证明 RRF 后仍需要 rerank，
  另建任务。
- 不承诺所有普通 PostgreSQL 部署都获得标准 BM25；本任务必须明确 Neon BM25 与 FTS
  fallback 的支持边界。

## Decision Required Before Start

- 推荐方案：生产 Neon 接受 `lakebase_text` 依赖并使用标准 BM25；本地/普通 PostgreSQL
  保留显式 `postgres_fts` fallback，通过同一 RRF contract 运行。若产品要求所有部署
  完全同算法，则需改为自建支持 BM25 扩展的 PostgreSQL 镜像/服务，范围和运维成本会
  显著扩大。
