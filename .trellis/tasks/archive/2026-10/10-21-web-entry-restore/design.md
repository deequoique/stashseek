# 技术设计：接回网页入口

## 1. 改动点

| 文件 | 改什么 |
|---|---|
| `extension/index.html` | 补 `<meta name="viewport">`、favicon、`<script type="module" src="/src/main.tsx">`，内容与 `workspace.html` 对齐，仅标题为"搜藏助手" |
| `extension/vite.config.ts` | `rollupOptions.input: ["index.html", "workspace.html", "sidepanel.html"]` |
| `deploy/scripts/notebook-agent-deploy` | 在 `pip install -e` 之后、`.release-built` 之前加两步 `runuser -u notebook-agent -- /usr/bin/env --chdir="$release_dir/extension" corepack pnpm install --frozen-lockfile` 与 `corepack pnpm build`；随后 `runuser -u notebook-agent -- mkdir -p "$release_dir/web"` 和 `ln -sfn ../extension/dist "$release_dir/web/dist"`。`set -e` 语义保证失败即退出，`current.next` 不会被链接 |
| `deploy/systemd/notebook-agent.service` | 在现有 `Environment=` 块加 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist`、`WEB_COOKIE_SECURE=true`、`WEB_FORWARDED_ALLOW_IPS=127.0.0.1` |
| `docs/reference/configuration.md`、部署文档（`docs/how-to/` 下现有的部署页，若无则在 `docs/reference/runtime-profiles.md` 补一节） | 修正说明，写发布手册的一次性主机步骤 |
| `tests/test_deployment_cli.py`（或同类静态断言测试） | 断言单元含四个 `WEB_*` 行；部署脚本含 `--chdir="$release_dir/extension"`、`corepack pnpm build`、`web/dist` 链接 |

不改：`app/api/app.py`（`_mount_spa` 保持 fail-fast）、`app/config.py`、页面代码、`copy-static.mjs`、`manifest.json`。

## 2. 为什么这样做

- **复用 `main.tsx`**：`workspace.html` 已经加载 `/src/main.tsx`，网页和插件工作区本来就是同一个 App，只是网页缺一个 `index.html` 入口。再建一套 `web/` 工程会让两边再次分叉（这次引用按钮就得移植两次）。
- **vite 多入口共享 chunk**：同一个 `vite build` 产出三个 html，assets 共享，`/assets` 由 `_mount_spa` 挂载，`chrome-extension://` 下的绝对路径 `/assets/...` 同样可用（现状已如此）。
- **兼容链接而不是先改主机**：主机单元是手工管理的，和部署不在一个事务里；`web/dist -> ../extension/dist` 让"旧单元 + 新代码"和"新单元 + 新代码"都能启动，把主机改动从发布关键路径上拿掉。
- **构建失败让部署失败**：和 main 的做法一致；静态目录缺失时 `_mount_spa` 抛错是最后一道保险，不放宽。

## 3. 本地验证方式

```bash
# 构建
cd extension && corepack pnpm install --frozen-lockfile && corepack pnpm build && ls dist/*.html
# 启动 API（本地 .env，不连生产以外的东西；只读 GET）
cd .. && WEB_SERVE_STATIC=true WEB_STATIC_DIR=extension/dist .venv/bin/python -m app.cli serve-web   # 以 app/cli.py 实际子命令为准
curl -sI http://127.0.0.1:<port>/ | head -1            # 200 text/html
curl -s http://127.0.0.1:<port>/ | grep -c 'main'        # index.html 引用的脚本
curl -sI http://127.0.0.1:<port>/privacy | head -1      # 200，不是 index.html
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<port>/api/does-not-exist   # 404
```

内置浏览器打开 `http://127.0.0.1:<port>/`，确认进入网页模式（登录页或视频库），截图存 `research/`。

注意：本地 `.env` 的 `DATABASE_URL` 是生产库，本地起服务只做上面的只读 GET，不登录、不写。

## 4. 回滚

- 代码：回退本任务提交；release 目录里的 `web/dist` 链接随目录一起废弃。
- 主机：若已把单元改成 `extension/dist`，改回 `web/dist` 对新代码同样可用（有链接），对旧代码（main）也可用。
