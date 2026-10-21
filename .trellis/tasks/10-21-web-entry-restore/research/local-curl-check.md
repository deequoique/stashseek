# 本地 curl 核对（2026-10-21）

环境：Neon 测试分支（`ep-falling-fog-*`），`WEB_SERVE_STATIC=true WEB_STATIC_DIR=extension/dist WEB_PORT=8765`，
`extension/dist` 由 `corepack pnpm build` 产出（含 `index.html`、`workspace.html`、`sidepanel.html`）。
只做只读 GET，没有登录、没有提交表单。

## A. 当前 dev 代码（`app/` 未改）：网页入口没有被挂载

命令：`.venv/bin/python -m app.cli web-server`

| 路径 | 结果 |
|---|---|
| `/` | 404 `application/json`（`{"code":"not_found",...}`） |
| `/assets/main-DVlVS-Gy.js` | 404 `application/json` |
| `/api/v1/health` | 200 json |
| `/privacy` | 200 html |
| `/api/does-not-exist` | 404 json |

原因：`app/api/runtime.py::build_api_app` 接受 `mount_static` 参数，但最后写死 `create_app(..., static_dir=None)`，
`WEB_SERVE_STATIC` / `WEB_STATIC_DIR` 从未被读取（`app/web_api.py` 同样 `static_dir=None`）。
main 的同一位置是 `static_dir=settings.web_static_dir if serve_static else None`。
所以仅靠构建、部署脚本、单元环境变量，网页版不会出现；这与父任务 PRD 里
"`_mount_spa` 抛 ValueError" 的描述不同（实际是静默 404，API 仍可用）。

## B. 模拟把 main 的接线补回（不改 `app/`，临时启动脚本包装 `create_app`）

| 路径 | 结果 |
|---|---|
| `/`、`/login`、`/library`、`/index.html` | 200 `text/html`（index.html） |
| `/` 正文 | 引用 6 个 `/assets/*`，全部 200（4 个 js 与 1 个 css 实测） |
| `/assets/main-DVlVS-Gy.js` | 200 `text/javascript` |
| `/assets/ingest-V4Fne5Bi.css` | 200 `text/css` |
| `/privacy` | 200 html，正文含"隐私"、不含 `id="root"` |
| `/privacy/privacy.css` | 200 css |
| `/api/does-not-exist`、`/api/v1/does-not-exist` | 404 `application/json` |
| `/api/v1/health` | 200 json |
| `/favicon.svg` | 200 但 `text/html`（落入 SPA 兜底，返回 index.html） |

`/favicon.svg` 的问题 main 同样存在：`_mount_spa` 只挂 `/assets`，`public/` 里的文件不会被单独提供。
不影响功能，只是浏览器标签页图标拿到 HTML；要修需要在 `_mount_spa` 里挂静态文件或把图标改成走 `/assets`。

## C. 补丁在隔离副本里的回归结果

在 scratchpad 的 `app/`+`tests/` 副本（不含 `extension/`，等同 CI 的后端步骤）应用 4 行补丁后：

- `tests/test_web_api_runtime.py tests/test_web_api_app.py tests/test_external_login_routes.py tests/test_asr_audio_route.py tests/test_web_cli.py tests/test_web_auth.py`：115 passed
- `tests/test_extension_auth.py tests/test_browser_companion_api.py tests/test_mcp_server.py tests/test_page_qa_api.py tests/test_privacy_page.py`：106 passed

## 2026-10-21 主会话：应用 `app/api/runtime.py` 接线修复后的浏览器核对

- 修复：`build_api_app` 恢复 `serve_static = settings.web_serve_static if mount_static is None else mount_static`，`create_app(..., static_dir=settings.web_static_dir if serve_static else None)`；新增测试 `tests/test_web_api_runtime.py::test_runtime_serves_the_website_bundle_when_static_is_enabled`。
- 启动：`python -m app.cli web-server`，`DATABASE_URL` 指向 Neon 测试分支，`WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist`、端口 8766。日志：`Application startup complete`。
- 内置浏览器：
  - `http://localhost:8766/` 渲染网页模式落地页（标题"搜藏助手"，导航：项目目的 / 使用流程 / 试用场景 / 登录 / 添加到 Chrome；主区 CTA 与离线安装包链接）。
  - `http://localhost:8766/#/login` 渲染邮箱登录页（邮箱输入框、"获取验证码"按钮、"获取浏览器插件"链接）。
  - 控制台无错误。未登录、未提交任何表单。
- pytest：`tests/test_web_api_runtime.py tests/test_web_api_app.py tests/test_linux_frontend_deployment.py tests/test_deployment_cli.py tests/test_production_caddy_deployment.py` 83 通过。
