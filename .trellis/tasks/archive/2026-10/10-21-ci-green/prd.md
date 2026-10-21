# 修复 CI：插件类型检查与 lint 全绿

父任务 `10-21-release-readiness` 的第 3 个子任务。轻量任务：本文含需求、验收与执行清单，不单独写 design。在子任务 1、2 完成后开工，最终确认要在两者提交之后再跑一遍。

## 目标

PR 到 main 时 `.github/workflows/extension-api-contract.yml` 全绿，并且在干净环境（无本地 `.env`）下 CI 的 pytest 子集全绿、全量 pytest 无收集错误。

## 已知事实（2026-10-21，dev d4bc689，Node 22.20，Python 3.11）

工作流步骤：`setup-python 3.11` + `pip install -e '.[dev]'`（不用 `uv.lock`）→ pytest 子集（`tests/test_extension_auth.py tests/test_browser_companion_api.py tests/test_web_api_runtime.py tests/test_web_api_app.py tests/test_mcp_server.py`）→ `alembic heads` → `setup-node 22.22.2` + `pnpm install --frozen-lockfile` → `pnpm check:api` → `pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit`。

- **`pnpm typecheck`（`tsconfig.test.json`）42 个错误**，全部在测试文件：
  - 32 个 `TS2532 Object is possibly 'undefined'`（`noUncheckedIndexedAccess` 下的数组/查询结果下标）：`FavoritesPicker.test.tsx` 10、`api/client.test.ts` 9、`PartsPicker.test.tsx` 7、`ChatPage.test.tsx` 4、`partSelection.test.ts` 1、`ExternalLoginPage.test.tsx` 1。
  - 4 个 `TS2786 'Boom' cannot be used as a JSX component`（`ErrorBoundary.test.tsx`）。
  - 2 个 `TS2488 NodeListOf<Element>` 不可迭代（`ChatPage.test.tsx`）。
  - 1 个 `TS2352` 对象字面量到类型的转换（`positioning.test.ts`）；2 个 `TS2322`（`partSelection.test.ts`、`FavoritesPicker.test.tsx`）；1 个 `TS18048`（`ChatPage.test.tsx`）。
  - 源码 `tsconfig.json` 已通过。
- **`pnpm lint` 3 个错误**：`src/account/AccountLinkPage.test.tsx:26` 未用变量 `user`；`src/api/client.ts:14-15` 未用导入 `EmailChallengeInput`、`EmailVerifyInput`。
- **`pnpm check:api` 失败**：`extension/src/api/openapi.json is stale`。本机 `scripts/export_web_openapi.py` 渲染出的 413 描述是 "Request Entity Too Large"，提交的 `openapi.json` 是 "Content Too Large"；本机 Starlette 0.52.1 / FastAPI 0.141.1 与 `uv.lock` 一致，但 CI 不用锁文件，装到的版本措辞可能不同。差异只有这一处。
- **pytest 子集**在本地受 `.env` 影响（`chrome-extension://*` 通配 origin 让 `config.py` 抛错）；CI 没有 `.env`。
- 全量 pytest 的收集错误（`tests/test_inspect_media_agent_tool.py`）由子任务 2 处理。
- dev 删除了 main 的 `.github/workflows/web-auth-contract.yml`（跑 55 分钟的 Postgres 认证契约），合并后消失；不恢复。

## 需求

- R1 类型错误：按文件修 42 处，只改测试文件；优先用非空断言辅助（例如 `expectDefined(x)`/`x!` 配合 `expect(...).toBeDefined()`）而不是放宽 `tsconfig.test.json`。`ErrorBoundary.test.tsx` 的 `Boom` 组件补返回类型；`NodeListOf` 用 `Array.from`；`positioning.test.ts` 的转换改成先声明类型。
- R2 lint：删掉 3 处未用的变量/导入。
- R3 `check:api`：让导出结果与 Starlette 版本无关——在 `scripts/export_web_openapi.py`（或 `app/api/app.py` 的 413 响应定义处）固定 413 的描述文本，然后 `pnpm generate:api` 重新生成 `openapi.json` 与 `schema.d.ts`，确认 `check:api` 在本机通过且生成物只有这一处变化。
- R4 干净环境验证：用一个不读本地 `.env` 的方式（例如 `env -i` 加最小变量，或临时把 `.env` 改名）跑 CI 的 pytest 子集和 `alembic heads`，全绿。
- R5 `pnpm build` 与 `pnpm run audit` 通过（子任务 1 已验证，最终再跑一次）。

## 验收标准

- [x] AC1 在 dev 上用 Node 22 依次跑 `pnpm check:api`、`pnpm typecheck`、`pnpm lint`、`pnpm test`、`pnpm build`、`pnpm run audit`，全部退出 0。
- [x] AC2 不读本地 `.env` 的环境下，CI 的 pytest 子集和 `alembic heads` 全绿；`pytest --collect-only` 无错误。
- [x] AC3 `tsconfig.test.json`、`eslint` 配置没有被放宽。
- [ ] AC4 开一个指向 main 的草稿 PR（标题注明 draft，不合并）让工作流跑一次，全绿后记录运行链接到 `research/`；是否保留该 PR 作为 release PR 的前身由用户决定。
- [ ] AC5 按路径提交到 dev。

## 执行清单

- [x] 0 前置：Node 22 + `corepack pnpm`；确认子任务 1、2 已提交。
- [x] 1 `pnpm lint` 三处；`pnpm typecheck` 按文件修完，逐文件跑 `pnpm exec tsc -p tsconfig.test.json --noEmit` 看余量。
- [x] 2 413 描述固定 + `pnpm generate:api`；检查 `git diff extension/src/api/openapi.json extension/src/api/schema.d.ts` 只有预期变化。
- [x] 3 干净环境跑 CI 的 pytest 子集与 `alembic heads`（方法写进 `research/`）。
- [x] 4 全套命令跑一遍（AC1）；`trellis-check`；按路径提交。
- [ ] 5 草稿 PR 跑 CI（需要 `gh pr create --draft`，先征得用户同意再推送分支）。

## 不在范围

- 恢复 `web-auth-contract.yml`。
- 修 `tests/test_item_management_tools.py` 等与 CI 子集无关的既有失败。
- 放宽类型或 lint 规则。
