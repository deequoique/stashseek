# 新增媒体资产与向量索引

## 目标
建立图片、视频和视频片段的持久化模型，使媒体向量与现有文本 Segment 解耦，同时保留租户、删除、归档和版本边界。

本子任务是整个父任务最后一个实现阶段：当前无本地 PostgreSQL 环境，因此先由前置子任务用 fixture/fake 完成协议验证，再执行 Alembic offline SQL 审查和生产数据库的非破坏性迁移。既有数据、表、字段和文本向量必须保留。

## 验收
- [ ] 有 `media_asset` 及向量版本/处理状态字段。
- [ ] 图片和视频片段可关联 ContentItem、Segment、对象存储 key 和时间范围。
- [ ] pgvector 维度按当前冻结的 WeMM 配置使用，不执行 benchmark。
- [ ] offline SQL、备份/快照确认、唯一哈希、租户过滤、删除竞态和旧数据兼容检查通过。

## 向量检索约束（由 `10-06-tenant-vector-filter-first` 引入，强制）

`media_embedding.embedding` **不得**声明全局 HNSW/IVFFlat 等近似索引
（已在该任务中移除 `ix_media_embedding_cosine_hnsw` 的迁移与 ORM 声明）。多租户表上的全局
ANN 索引会先在全表上做近似候选扫描，再按租户过滤，当某租户的行不在近似候选窗口内时会被
静默截断。

实现媒体检索 SQL 时必须：

- 复用 `app/retrieval/search.py` 中的 `filter_first_vector_rank` 做排序，而不是
  重新实现一遍排序逻辑；
- 候选集合（传给 `filter_first_vector_rank` 的 `candidates`）必须先用
  `app_user_id = :tenant` 加上 embedding 空间列（`model`、`revision`、
  `protocol_version`、`dimensions`，对应 `ix_media_embedding_user_space`）过滤到单一
  租户，排序前置过滤之后，而不是先排序再过滤；
- 参见 `.trellis/spec/backend/agent-retrieval-convergence.md`「Tenant-scoped vector
  search must filter before ranking」与
  `.trellis/spec/backend/database-guidelines.md`「Vector indexes in a multi-tenant
  table」两个 scenario，二者为本约束的权威定义。
