# Git 提交计划

确认记录：用户在上述三组计划展示后回复“继续”，授权按此计划完成本地提交。
2026-10-16 开始执行；保留列出的排除项。下文为当时展示的原计划。

执行核对补充：AppShell 实际只有 SSO 标签变化，归入第 2 组；能力查询 helper
在 LoginPage/LibraryPage 的调用点及通用 QueryClient observer 重挂载是历史预加载
的必要依赖，随第 1 组。共享文件均在临时累计快照中验证后按内容暂存。
发现 Python 3.11/3.13 默认 422 描述不同会使 CI 契约检查失败，因此第 1 组还
包含 exporter 的精确默认文案规范化、两个回归测试及对应 type-safety 规范；
生成文件继续由 canonical exporter 生成，没有手工修改。

这里只是计划，尚未暂存、提交、推送或发布。请一次确认整批计划；也可回复
“我自己来”，保留当前文件交由你提交。

当前工作区还包含本对话之前开发的聊天/历史检索和 SSO。它们没有被回退。
分 P 迁移 f8a9b0c1d2e3 明确依赖此前尚未提交的 SSO 迁移 e2f3a4b5c6d7，
因此建议按下面三组顺序整理，避免仅提交后继迁移而丢失前置版本。
这三组包含之前的功能，不会全部归名为本次收藏夹改动。

## 拟提交 1

`fix(web): improve chat keyboard, viewport and history loading`

完整文件：

- web/src/app/AppShell.tsx
- web/src/app/AppShell.test.tsx
- web/src/chat/ChatPage.tsx
- web/src/chat/ChatPage.test.tsx
- web/src/chat/conversationQueries.ts
- web/src/chat/conversationQueries.test.ts
- .trellis/spec/frontend/component-guidelines.md
- .trellis/tasks/10-14-history-loading-performance/

共享文件仅取聊天、历史加载、路由预加载/懒加载相关部分：

- app/api/conversation_routes.py：历史读取列投影，排除本次来源改写。
- tests/test_conversation_list_queries.py：对应历史查询断言。
- web/src/app/App.tsx、App.test.ts：历史预加载与普通页面懒加载。
- web/src/api/client.ts、client.test.ts：既有读取的取消信号行为。
- web/src/styles.css：固定聊天视口、独立滚动、返回资料库按钮及边线调整。
- .trellis/spec/frontend/hook-guidelines.md、state-management.md：对应既有约定。

## 拟提交 2

`feat(auth): add Microsoft, WeChat and QQ website sign-in`

完整文件或目录：

- .env.example
- app/api/auth_schemas.py
- app/api/email_auth_routes.py
- app/api/external_auth_routes.py
- app/channels/identity.py
- app/cli.py
- app/config.py
- app/external_auth.py
- app/external_providers/
- app/web/auth.py
- app/web_auth.py
- migrations/versions/e2f3a4b5c6d7_external_login_foundation.py
- pyproject.toml、uv.lock
- tests/test_external_login.py
- tests/test_external_login_models.py：先取 SSO 原有测试，分 P SQLite 列补齐随第 3 组。
- tests/test_external_login_postgres.py
- tests/test_external_login_routes.py
- tests/test_external_providers.py
- tests/test_web_auth_postgres.py
- tests/test_web_session_cache.py
- tests/test_web_api_runtime.py
- web/src/auth/LoginPage.tsx、LoginPage.test.tsx
- web/src/auth/ExternalLoginMethods.tsx
- web/src/auth/ExternalLoginPage.tsx、ExternalLoginPage.test.tsx
- .trellis/spec/backend/external-login.md
- .trellis/spec/backend/channel-identity-linking.md
- .trellis/spec/frontend/external-login.md
- docs/README.md、docs/how-to/README.md、docs/reference/README.md
- docs/how-to/configure-external-login.md
- docs/reference/configuration.md
- .trellis/tasks/10-15-external-login-foundation/
- .trellis/tasks/10-15-microsoft-login/
- .trellis/tasks/10-15-multi-provider-login/
- .trellis/tasks/10-15-qq-website-login/
- .trellis/tasks/10-15-wechat-website-login/

共享文件仅取外部身份、登录/会话边界及对应文档：

- app/models.py：外部身份/流程、Session/email anchor 字段与约束；不含分 P 列。
- app/api/app.py、runtime.py：外部登录能力、路由、CSP 与组合。
- scripts/export_web_openapi.py：外部登录导出服务。
- tests/test_web_api_public.py：外部登录能力断言。
- tests/test_web_auth_migration.py：前置 SSO 版本链断言。
- web/src/app/App.tsx、App.test.ts：外部登录路由和私有缓存轮换。
- web/src/api/client.ts、client.test.ts、contracts.ts：外部登录和 Session 边界。
- web/src/styles.css：外部登录控件样式。
- .trellis/spec/backend/index.md、web-browser-runtime.md 与 frontend/index.md。
- docs/how-to/use-web-library.md、docs/reference/web-api.md：登录部分。

此提交表示代码接入，不表示已有平台凭据或线上登录验收通过。

## 拟提交 3

`feat(bilibili): import public favorites and preserve multipart sources`

完整文件或目录：

- app/agent/runtime_state.py、services.py、types.py
- app/api/bilibili_routes.py、bilibili_schemas.py
- app/bilibili/
- app/browser_capture.py
- app/connectors/base.py、bilibili.py、bilibili_extract.py
- app/ingest/submission.py、tasks.py
- app/web/library.py、transcript.py、source_projection.py
- migrations/versions/f8a9b0c1d2e3_bilibili_part_sources.py
- tests/test_agent_context.py
- tests/test_bilibili_url.py
- tests/test_bilibili_parts_core.py、test_bilibili_parts_api.py
- tests/test_bilibili_parts_postgres.py、test_bilibili_multipart_extract.py
- tests/test_bilibili_favorites.py、test_bilibili_gate_redis.py
- tests/test_browser_source_projection.py
- tests/test_ingest_submission.py
- tests/test_item_management_tools.py：仅新来源列的既有 SQLite 建表补齐。
- tests/test_web_transcript.py
- web/src/library/AddVideosDialog.tsx、AddVideosDialog.test.tsx
- web/src/library/PartsPicker.tsx、PartsPicker.test.tsx
- web/src/library/FavoritesPicker.tsx、FavoritesPicker.test.tsx
- web/src/library/partSelection.ts、partSelection.test.ts
- web/src/library/videoInput.ts、videoInput.test.ts
- web/src/library/LibraryPage.tsx、LibraryPage.test.tsx
- web/src/videos/VideoDetailView.tsx、VideoDetailView.test.tsx
- .trellis/spec/backend/bilibili-connector.md、bilibili-parts.md
- .trellis/spec/frontend/bilibili-import.md
- .trellis/tasks/10-15-bilibili-bv-input/
- .trellis/tasks/10-15-bilibili-favorites-import/
- .trellis/tasks/10-16-bilibili-multipart/
- .trellis/tasks/10-16-bilibili-favorites-preview/

共享文件仅取本次分 P、收藏夹、来源导航与契约部分：

- app/models.py：完整 nullable BV/CID/page 字段、约束、索引和 String 导入。
- app/api/app.py、runtime.py：B 站服务、能力、安全错误和路由。
- app/api/conversation_routes.py：JSON/SSE/历史的批量可信来源投影。
- scripts/export_web_openapi.py：B 站惰性导出占位服务。
- tests/test_conversation_list_queries.py：来源投影批量查询断言。
- tests/test_web_api_public.py、test_web_auth_migration.py、test_external_login_models.py：B 站能力、单一后继版本及来源列补齐。
- web/src/api/client.ts、client.test.ts、contracts.ts：收藏夹、分 P、来源字段。
- web/src/styles.css：选择列表、弹窗和粘性选择状态/确认栏。
- .trellis/spec/backend/index.md、web-browser-runtime.md、frontend/index.md、hook-guidelines.md、state-management.md：本次约定。
- docs/how-to/use-web-library.md、docs/reference/web-api.md：分 P/收藏夹操作和 API。

## 生成文件及分批方式

web/src/api/openapi.json、schema.d.ts 是共享的生成文件。第 2/3 组分别从该组
准备提交的规范重新生成；不手工剪贴响应类型。共享源文件按上列行为/字段拆分，
不会整文件一并收纳其他组的改动。提交前核对暂存差异和必要检查，最终文件内容
应与已验收的工作区一致。若无法可靠拆分，停止提交并报告，不猜测归属。

没有使用已丢失的临时快照作字节级比较。计划依据当前 diff、实现归属和回归
证据；不会 reset/restore 工作区、amend 或 push。

## 不纳入任何提交的未确认文件

- .trellis/tasks/10-14-email-login-delivery/
- .trellis/tasks/10-14-verify-local-runtime/

它们是此前留下的任务记录，本次没有确认其内容归属。默认保留、排除，不删除。
以上提交不执行生产迁移、不导入真实收藏夹，也不会发布线上程序。

## 确认方式

同意上述三组与排除项，可回复“行，按计划提交”。如希望自己处理，回复
“我自己来”。本计划没有默认获得提交授权；归档和日志收尾在工作提交之后进行。

## 执行结果 — 2026-10-16

用户回复“继续”后，三组已按顺序本地提交：`b4d60fe`、`2bb6dbc`、
`788b2ae`。每组实际暂存集合与内容均经独立审查。上面的“尚未暂存”和
确认方式保留为原计划记录；现已完成授权。排除的两个任务目录保持原样。
没有 push、线上发布、生产迁移或真实收藏夹导入。
