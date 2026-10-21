# 修复 CI：插件类型检查与 lint 全绿

父任务 `10-21-release-readiness` 的第 3 个子任务，在 1、2 完成后再跑最终确认。待规划（本文只记范围与已知事实）。

## 已知事实（2026-10-21，dev 26d4267，Node 22.20）
- `.github/workflows/extension-api-contract.yml` 在 PR 到 main 时跑：pytest 子集（`tests/test_extension_auth.py tests/test_browser_companion_api.py tests/test_web_api_runtime.py tests/test_web_api_app.py tests/test_mcp_server.py`）、`alembic heads`、`pnpm check:api`、`pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit`。
- `pnpm typecheck` 42 个错误，全部在 `tsconfig.test.json` 覆盖的测试文件：`FavoritesPicker.test`、`PartsPicker.test`、`positioning.test`、`ChatPage.test`（7 处旧的）。
- `pnpm lint` 3 个错误：`AccountLinkPage.test.tsx` 的 `user` 未用，`api/client.ts` 的 `EmailChallengeInput`、`EmailVerifyInput` 未用导入。
- `pnpm check:api` 本地失败于 Starlette 对 413 的措辞（"Content Too Large" 对 "Request Entity Too Large"），CI 用锁定版本可能不同，需在 CI 环境确认。
- dev 删除了 main 的 `web-auth-contract.yml`，合并后消失。

## 范围
- 修掉 42 个类型错误和 3 个 lint 错误；`pnpm build`、`pnpm run audit` 通过。
- 确认 `check:api` 在 CI 的依赖版本下通过，或把 413 描述做成版本无关。
- CI 子集 pytest 在干净环境（无本地 `.env`）全绿；全量 pytest 无收集错误。

## 验收（草案）
- [ ] 在 dev 上用 Node 22 跑工作流里的全部命令，全部退出 0。
- [ ] 开一个指向 main 的草稿 PR 让 CI 跑一次，全绿后关闭草稿（或留作 release PR 的前身）。
