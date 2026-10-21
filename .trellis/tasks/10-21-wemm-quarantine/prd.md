# 隔离 WeMM 半成品：摘掉迁移、确认开关关闭无暴露

父任务 `10-21-release-readiness` 的第 2 个子任务，可与第 1 个并行；第 3 个子任务（CI）在本任务完成后做最终确认。本文只写需求与验收；技术方案在 `design.md`，执行计划在 `implement.md`。

## 目标

本次 dev 合 main 的发布不把 WeMM 多模态的半成品带上线：生产不建 WeMM 的表，开关关闭时没有任何媒体检索的接口、工具或入库路径，测试套件没有收集错误。WeMM 更换 embedding 模型后另立任务重做。

## 背景与现状（2026-10-21 在 dev 上确认）

- `3b64d34 wip(wemm)`：50 个文件、22,833 行，其中 `docs/agent-flow.html` 14,895 行、测试约 7,000 行。运行时足迹：`app/models.py`（`MediaAsset`、`MediaEmbedding`，`embedding` 是 `Vector(1024)`）、`app/retrieval/media.py`（`MediaRepository` 接口与 `MediaRetrievalService`，不引用 ORM 模型）、`app/ingest/media.py`（只被 `tests/test_media_embedding_ingestion.py` 引用）、迁移 `c9d0e1f2a3b4_media_asset_embeddings.py`（建 `media_asset`、`media_embedding`，外键到 `content_item`/`app_user`）。
- 迁移链：`d04fdae36884 -> c9d0e1f2a3b4 -> cff884c74a1e -> e2f3a4b5c6d7 -> f8a9b0c1d2e3`；后三个迁移都不引用媒体表。生产在 `d04fdae36884`；Neon 测试分支已在 `f8a9b0c1d2e3`（2026-10-20 升级，所以测试分支上已经有这两张表）。
- 开关：`MULTIMODAL_RETRIEVAL_ENABLED`、`WEMM_ENABLED` 默认 false；`app/bootstrap.py:63` 在任一关闭时不构造媒体检索服务，`:92` 只在开启时把 `search_media`/`inspect_media` 注册给 Agent。
- 公开接口：`extension/src/api/openapi.json` dev 相对 main 新增的路径全是 `/api/v1/auth/external/*` 和 `/api/v1/bilibili/*`，没有媒体接口。
- `tests/test_inspect_media_agent_tool.py` 收集阶段报 `MediaCandidate.__init__() missing 'item_id'`：`app/agent/media.py:92` 的 `MediaCandidate` 有 `item_id` 字段，测试的构造辅助函数漏传。
- `tests/test_migration_roundtrip_postgres.py` 只做 upgrade/downgrade/upgrade，不比较 ORM 元数据，所以迁移链可以摘掉媒体迁移而不必同时删模型；但留下无表的模型会误导后续工作。
- 用户 2026-10-21 决定：WeMM 更换 embedding 模型，现有半成品不能原样上线。

## 需求

### R1 迁移链里没有媒体表
- 删除 `migrations/versions/c9d0e1f2a3b4_media_asset_embeddings.py`，`cff884c74a1e` 的 `down_revision` 改回 `d04fdae36884`；`alembic heads` 单头，`alembic history` 连续。
- Neon 测试分支：`DROP TABLE media_embedding, media_asset`（它们由被删的迁移创建，生产没有），`alembic_version` 保持 `f8a9b0c1d2e3`；`tests/test_migration_roundtrip_postgres.py` 在测试分支通过。

### R2 没有无表的 ORM 与入库代码
- 删除 `app/models.py` 里的 `MediaAsset`、`MediaEmbedding`，删除 `app/ingest/media.py` 和 `tests/test_media_embedding_ingestion.py`；保留 `app/retrieval/media.py`、`app/agent/media.py` 及其基于假对象的测试（它们是接口层，不依赖表）。
- 相关文档（`docs/explanation/ingestion-and-retrieval.md`、`docs/reference/configuration.md` 里 WEMM 段）注明：媒体表与入库在更换 embedding 模型的任务里重做，当前只保留接口。

### R3 开关关闭时无暴露
- 证明：`MULTIMODAL_RETRIEVAL_ENABLED=false` 时 Agent 工具集不含 `search_media`、`inspect_media`；导出的 OpenAPI 不含媒体路径；入库不写任何媒体表。用现有测试或新增断言覆盖。
- `WEMM_*` 配置项保留（读取不报错，默认值合法），生产 env 不需要新增键。

### R4 测试套件无收集错误
- `tests/test_inspect_media_agent_tool.py` 的构造辅助补 `item_id`，整个文件能收集并通过；全量 `pytest --collect-only` 无错误。

## 验收标准
- [ ] AC1 `alembic heads` 单头为 `f8a9b0c1d2e3`，`alembic history` 里没有 `c9d0e1f2a3b4`；测试分支上 `tests/test_migration_roundtrip_postgres.py` 通过，且 `\dt media_*` 为空。
- [ ] AC2 仓库里没有 `MediaAsset`/`MediaEmbedding`/`app/ingest/media.py`；`grep -rn 'media_asset\|media_embedding' app migrations` 为空。
- [ ] AC3 开关关闭时的 Agent 工具清单和 OpenAPI 与 main 的媒体部分一致（无媒体项），有测试断言。
- [ ] AC4 `pytest --collect-only -q` 无错误；`tests/test_inspect_media_agent_tool.py` 通过。
- [ ] AC5 按路径提交到 dev；文档更新。

## 不在范围
- 更换 embedding 模型、重做媒体入库与检索（另立任务）。
- 删除 `docs/agent-flow.html` 等文档产物。
- 删除 `app/retrieval/media.py`、`app/agent/media.py` 和 Agent 的媒体工具代码（保留接口层，减少以后重做的改动面）。

## 约束
- dev 分支；按路径提交；其它会话可能并行改 `app/`，提交前核对。
- 只能对 Neon 测试分支执行 DDL，绝对不碰生产库。
