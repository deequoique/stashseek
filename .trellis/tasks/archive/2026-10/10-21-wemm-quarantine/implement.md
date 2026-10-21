# 执行计划：隔离 WeMM 半成品

实现由 `trellis-implement` 子代理（model sonnet）完成，检查由 `trellis-check` 完成；派发提示第一行写 `Active task: .trellis/tasks/10-21-wemm-quarantine`。

## 0. 前置
- [x] 测试库环境变量（只连 Neon 测试分支）：
```bash
export DATABASE_URL="$(grep '^DATABASE_URL=' .env.neon-test | cut -d= -f2- | tr -d "\"'" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql+psycopg://#')"
case "$DATABASE_URL" in postgresql+psycopg://*@ep-falling-fog-*) ;; *) echo "not the test branch"; exit 1;; esac
```
- [x] 确认 `git status` 里没有别的会话对 `app/models.py`、`migrations/` 的未提交改动。

## 1. 迁移与模型
- [x] 删除 `migrations/versions/c9d0e1f2a3b4_media_asset_embeddings.py`；`cff884c74a1e_ingest_status_stages.py` 的 `down_revision` 改为 `"d04fdae36884"`；`alembic heads`、`alembic history` 核对。
- [x] `app/models.py` 删除 `MediaAsset`、`MediaEmbedding` 及专用 import；删除 `app/ingest/media.py`、`tests/test_media_embedding_ingestion.py`。
- [x] `grep -rn 'media_asset\|media_embedding\|MediaAsset\|MediaEmbedding\|app.ingest.media' app migrations tests docs` 清理剩余引用（文档改措辞，代码删引用）。
- [x] 测试分支一次性 DDL：`DROP TABLE IF EXISTS media_embedding; DROP TABLE IF EXISTS media_asset;`，并确认 `alembic_version` 仍为 `f8a9b0c1d2e3`；把执行的 SQL 和结果记到 `research/test-branch-ddl.md`。

**门 1**：`tests/test_migration_roundtrip_postgres.py`、`tests/test_tasks.py`、`tests/test_asr_ingest_flow.py` 通过。

## 2. 无暴露与收集错误
- [x] `tests/test_inspect_media_agent_tool.py`：构造辅助补 `item_id`；整个文件通过。
- [x] 新增断言：`build_knowledge_agent`/`build_agent` 在 `multimodal_retrieval_enabled=False` 下的工具名集合不含 `search_media`、`inspect_media`（找现有的工具清单测试复用，例如 `test_bounded_autonomy_instructions_never_name_an_unregistered_tool` 旁边）；`scripts/export_web_openapi.py` 导出的路径集合不含 `media`（若已有 OpenAPI 快照测试，补一行）。
- [x] `.venv/bin/python -m pytest -q --collect-only` 无 error。

**门 2**：`tests/test_inspect_media_agent_tool.py tests/test_multimodal_retrieval_rollout.py tests/test_media_answer_flow.py tests/test_agent_runtime.py` 通过（既有的 `.env` 相关失败除外）。

## 3. 文档、检查、提交
- [x] `docs/explanation/ingestion-and-retrieval.md`、`docs/reference/configuration.md`（WEMM 段）：注明媒体表与入库在更换 embedding 模型的任务里重做，当前只保留接口；`WEMM_*`、`MULTIMODAL_RETRIEVAL_ENABLED` 保持默认关闭。
- [x] 运行 `trellis-check`。
- [x] 按路径提交到 dev（数组展开）：`migrations/versions app/models.py app/ingest/media.py tests/test_media_embedding_ingestion.py tests/test_inspect_media_agent_tool.py <新增断言文件> docs/explanation/ingestion-and-retrieval.md docs/reference/configuration.md .trellis/tasks/10-21-wemm-quarantine`（删除的文件用 `git rm` 后同样列在路径里）。

## 风险
| 风险 | 处理 |
|---|---|
| 别的会话正在继续 WeMM 工作 | 开工前 `git log -3 -- app/retrieval/media.py app/models.py`，有新提交先沟通 |
| 删模型后有隐藏引用 | 第 1 步的 grep 加 `--collect-only` 兜底 |
| 测试分支 DDL 误连生产 | 命令前固定做主机校验 |

## 开工前检查
- `prd.md`、`design.md`、`implement.md` 已被用户确认；jsonl 有真实条目。
- `python3 ./.trellis/scripts/task.py start .trellis/tasks/10-21-wemm-quarantine` 只在用户确认后执行。
