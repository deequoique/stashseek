# ASR 转写链路：实施计划

## 规则

- 在 `dev` 分支开发。具体代码由 Sonnet 子 agent（`trellis-implement`，`model: sonnet`）编写；主会话负责审查、验证和提交。
- 子 agent 不得提交，也不得修改本任务范围以外的文件。
- 工作区里有大量与本任务无关的未提交改动：
  - 不要回退或格式化这些改动；
  - 修改共享文件时，只做本任务需要的最小编辑。
- 测试必须设置 `DATABASE_URL` 为 `.env.neon-test` 中的测试分支 DSN（主机为 `ep-falling-fog-azrht8ir-pooler…`）。不要运行裸 `pytest`，也不要运行全量测试。
- 不调用任何真实供应商。DashScope、OpenAI 和 yt-dlp 一律注入 fake transport 或 fake runner。

## 阶段 1：A 和 B 并行（文件互不重叠）

### A. 存储、签名与公开路由

- [ ] `app/config.py`：按 design 的表格加入 ASR 字段和开启时的校验。
- [ ] `app/object_store.py`：新增 `put_file`、`open_stream`、`ObjectStream`、`ObjectNotFoundError`。
- [ ] `app/asr/__init__.py`（空文件）和 `app/asr/signing.py`。
- [ ] `app/api/app.py`：注册 `GET`/`HEAD` `/api/v1/asr-audio/{name}`，必须早于 SPA 兜底路由，并确认不会被中间件拦截。
- [ ] 测试：
  - `tests/test_asr_config.py`
  - `tests/test_asr_signing.py`
  - `tests/test_asr_audio_route.py`，用 TestClient 和 fake 存储
  - 对象存储新方法的测试，用 fake boto3 client

### B. ASR 包（只新增文件，不修改已有文件）

- [ ] `app/asr/types.py`、`app/asr/audio.py`、`app/asr/funasr.py`、`app/asr/whisper.py`、`app/asr/transcriber.py`、`app/asr/connector.py`。
- [ ] 与 A 的约定：
  - 只按 design 中写好的字段名使用 `Settings` 和对象存储的方法；
  - 测试中用 `SimpleNamespace` 或 fake 对象替代，不依赖 A 的实现；
  - `app/asr/__init__.py` 由 A 创建；B 如果需要可以创建同名空文件，内容必须为空。
- [ ] 测试：
  - `tests/test_asr_funasr.py`
  - `tests/test_asr_whisper.py`
  - `tests/test_asr_audio_download.py`
  - `tests/test_asr_transcriber.py`
  - `tests/test_asr_connector.py`
- [ ] 测试要覆盖：PRD 验收标准中关于 Fun-ASR 和备选的全部条目；临时对象在成功、失败和异常时都被清理；日志不含 URL、密钥或文本。

## 阶段 2：C 集成（A 和 B 合入并通过审查之后）

- [ ] `app/ingest/tasks.py`：
  - 新增 `asr` 队列和路由；
  - `process_item` 支持 `capture_v1` 和 `asr_pending`；
  - `process_dispatch` 处理 handoff；
  - 新增 `transcribe_dispatch_task`；
  - `AsrError` 错误码映射；
  - 诊断允许列表。
- [ ] `app/web/library.py` 的 `SAFE_ERROR_CODES`；检查 `app/ingest/notifications.py` 遇到未知失败码时的通用文案。
- [ ] 部署：
  - `deploy/systemd/notebook-agent-asr-worker.service`
  - `deploy/scripts/notebook-agent-deploy`
  - `app/deployment.py` 的本地 worker 队列
  - 对应的部署测试
- [ ] `app/cli.py` 新增 `asr-backfill` 命令，并补测试。
- [ ] 文档：`.env.example`、`docs/reference/configuration.md`，以及运维文档（ffmpeg、unit、MinIO 生命周期规则）。
- [ ] 测试：
  - `tests/test_asr_ingest_flow.py`：用 fake DB 和 dispatch 端到端验证 handoff → ready、单一完成事件、发布失败时的回退、ASR 关闭时行为不变、空转写失败为 `asr_no_speech`；
  - 更新 `tests/test_tasks.py` 和部署测试中受影响的断言。

## 阶段 3：检查与收尾（主会话）

- [ ] `trellis-check`（Sonnet）：对照 spec 和 PRD 审查。
- [ ] 运行验证命令：

  ```bash
  DATABASE_URL=<test DSN> uv run --frozen pytest -q tests/test_asr_*.py tests/test_tasks.py tests/test_deployment_cli.py tests/test_production_caddy_deployment.py tests/test_object_store*.py tests/test_web_api_app.py
  ```

  再加上 `ruff check`，对象是改动过的文件，并且只在仓库已配置 ruff 时运行。
- [ ] 新增后端 spec `.trellis/spec/backend/asr-transcription.md`，并更新 `index.md`。
- [ ] 提交到 `dev`：
  - 共享文件只暂存本任务的 hunk（以开工前的工作区快照为基准，计算差异后单独暂存）；
  - 新文件逐个加入。

## 回滚点

- 阶段 1 和阶段 2 都只增加代码路径，并且默认关闭；任何时候设置 `ASR_ENABLED=false` 都会回到现有行为。
- 如果需要整体放弃，回退本任务提交，并删除 `app/asr/` 和 asr worker unit。
