# 干净环境复跑 CI 步骤（2026-10-21，dev 8b7c12a + 本任务改动）

目的：证明 `.github/workflows/extension-api-contract.yml` 的后端步骤在没有本地 `.env` 的环境里全绿。

## 方法

- 把 `.env` 改名为 `.env.ci-hidden`，命令跑完立刻改回（`ls -la .env` 确认已还原）。不要用 `trap ... EXIT` 管理还原：Bash 工具里 trap 会在该次调用结束时才触发，期间的命令仍然读不到 `.env`，但也容易让下一次调用误以为还原了。直接在同一条命令里 `mv` 回去最稳。
- 环境变量照抄工作流 job 级 `env`：
  `STASHSEEK_LOG_RETRIEVAL_CONTENT=false PYTHONUNBUFFERED=1 DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:5432/kb REDIS_URL=redis://127.0.0.1:6379/0 STASHSEEK_ENV=development WEB_AUTH_ENABLED=false`
  - 本机 5432 没有监听，等同于 CI（job 没有 Postgres 服务），证明这几个测试不连库，也不可能碰到生产库。
  - 本机 6379 有 Redis 在监听，而 CI 没有；为了和 CI 一样"没有 Redis"，把 `REDIS_URL` 改指向无监听的 6390 端口。
- 两套解释器各跑一遍：
  1. 项目 `.venv`（Python 3.12.13，`uv.lock` 版本，Starlette 0.52.1）。
  2. 临时 venv（Python 3.11.4，和 CI 的 3.11 同代），依赖按 `pyproject.toml` 的 `dependencies` + `optional-dependencies.dev` 逐条 `pip install -r`，不用锁文件，等价于 CI 的 `pip install -e '.[dev]'`。这次装到 FastAPI 0.141.1、Starlette 1.9.0、Pydantic 2.14.1、pytest 9.1.1、Alembic 1.18.5；Starlette 与锁文件差了一个大版本，但导出和测试结果相同。venv 放在会话临时目录，没有写进仓库。

## 结果

| 步骤 | `.venv`（3.12，锁文件版本） | 临时 venv（3.11.4，未锁版本） |
|---|---|---|
| pytest 子集（`test_extension_auth`、`test_browser_companion_api`、`test_web_api_runtime`、`test_web_api_app`、`test_mcp_server`） | 55 passed | 55 passed |
| `alembic heads` | `f8a9b0c1d2e3 (head)`，单头 | `f8a9b0c1d2e3 (head)`，单头 |
| `scripts/export_web_openapi.py --check`（`check:api` 的实质） | 退出 0 | 退出 0 |
| `tests/test_web_openapi_export.py` | 3 passed | 3 passed |
| `pytest --collect-only`（全量） | 2891 collected，无收集错误 | 未跑 |

## 备注

- 413 的措辞：Python 3.13 把标准短语 `Request Entity Too Large` 改成 `Content Too Large`（422 同理，`Unprocessable Entity` → `Unprocessable Content`）。FastAPI 在没有 `description` 的 `responses={413: {...}}` 上用 `http.client.responses` 补默认值。声明 413 的只有 `app/page_qa/routes.py` 的页面问答路由。`scripts/export_web_openapi.py` 现在统一把这两个状态的默认措辞规范化（`STANDARD_PHRASES`），自定义描述原样保留。提交的 `openapi.json` 此前是 3.13 生成的，所以在 3.11/3.12 上一直 stale；重新生成后只改了 `openapi.json` 与 `schema.d.ts` 各一行。
- 之前在带 `.env` 的机器上失败的 `tests/test_web_api_app.py` 用例（`chrome-extension://*` 通配 origin），在没有 `.env` 时全部通过。
