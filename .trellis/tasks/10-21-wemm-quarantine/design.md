# 技术设计：隔离 WeMM 半成品

## 1. 改动点

| 文件 | 改什么 |
|---|---|
| `migrations/versions/c9d0e1f2a3b4_media_asset_embeddings.py` | 删除 |
| `migrations/versions/cff884c74a1e_ingest_status_stages.py` | `down_revision = "d04fdae36884"` |
| `app/models.py` | 删除 `MediaAsset`、`MediaEmbedding` 两个类及只为它们服务的 import |
| `app/ingest/media.py`、`tests/test_media_embedding_ingestion.py` | 删除 |
| `tests/test_inspect_media_agent_tool.py` | 构造辅助补 `item_id` |
| 新增断言（放在 `tests/test_multimodal_retrieval_rollout.py` 或 `tests/test_agent_runtime.py`） | 开关关闭时工具集不含 `search_media`/`inspect_media`；OpenAPI 路径集合不含 `media` |
| `docs/explanation/ingestion-and-retrieval.md`、`docs/reference/configuration.md` | 说明媒体表与入库待重做 |
| Neon 测试分支 | `DROP TABLE IF EXISTS media_embedding; DROP TABLE IF EXISTS media_asset;`（一次性运维，记录在 `research/`） |

## 2. 为什么这样做

- **摘掉迁移而不是改成空迁移**：空迁移会在生产留下一个没有意义的版本号，之后换模型重做时反而要绕开它；直接删除并把下游 `down_revision` 指回 `d04fdae36884`，链路和 main 的差异只剩真正要上线的三个迁移。
- **同时删模型**：`tests/test_migration_roundtrip_postgres.py` 不比较元数据，所以留着模型也能过测试，但 `Base.metadata` 里会有生产不存在的表，任何 `create_all` 或后续 autogenerate 都会被误导。模型只被 `app/ingest/media.py` 用到，一起删最干净。
- **保留接口层**：`app/retrieval/media.py`（`MediaRepository` 协议、`MediaRetrievalService`）和 `app/agent/media.py`（候选、证据类型）不依赖表，且 Agent 工具代码已被 `bootstrap.py` 的开关隔离；删了它们换模型时还得重写。
- **测试分支要同步 DDL**：分支上已有这两张表，迁移链摘掉后 downgrade 路径不会再管它们，`roundtrip` 测试又会在同一服务器 `CREATE DATABASE` 跑完整链，不受影响；但为了让测试分支结构和"生产部署后"一致，手工把表删掉。

## 3. 验证
```bash
.venv/bin/alembic heads && .venv/bin/alembic history | head -8
grep -rn 'media_asset\|media_embedding\|MediaAsset\|MediaEmbedding' app migrations   # 应为空
.venv/bin/python -m pytest -q --collect-only 2>&1 | tail -3                       # 无 error
.venv/bin/python -m pytest -q tests/test_inspect_media_agent_tool.py tests/test_multimodal_retrieval_rollout.py tests/test_agent_runtime.py tests/test_media_answer_flow.py tests/test_migration_roundtrip_postgres.py
.venv/bin/python scripts/export_web_openapi.py | grep -c '"/api/v1/media'          # 0（以脚本实际用法为准）
```
测试库 DDL 与 pytest 都只在 Neon 测试分支（`ep-falling-fog-*`）上执行。

## 4. 回滚
- 代码回退即可；测试分支的表可用被删迁移的 `upgrade()` 重建（保留在 git 历史里）。
