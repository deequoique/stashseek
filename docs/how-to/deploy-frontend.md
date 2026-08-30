# 部署独立前端

`web/` 是 StashSeek Chat 的私有 React 应用，不是可独立发布的组件库。它的部署
产物是 `web/dist`；前端和 `/api/v1` 契约应由同一个 release 一起审查和发布。

## 选择部署形态

| 形态 | 浏览器看到的 origin | Python 进程 |
| --- | --- | --- |
| bundled | 一个 origin，Python 同时提供 SPA、API、MCP/或 Web | `WEB_SERVE_STATIC=true` |
| split | 一个 origin；静态服务提供 SPA，反向代理把 `/api/v1/*` 转到 loopback | `WEB_SERVE_STATIC=false` |

两种形态都要求浏览器只看到一个精确 origin。不要让前端从一个公开 origin 直接请求
第二个 API origin，也不要加 wildcard CORS。

## 1. 构建并检查 `web/dist`

在包含目标 commit 的工作区执行：

```bash
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build
```

只有当 `check:api`、测试、类型检查、lint 和 build 全部成功时，才把 `web/dist`
交给静态服务器或打进 combined release。不要把真实 `.env`、邮件凭据或浏览器 cookie
复制到 dist。

## 2. Bundled 部署

后端环境示例：

```dotenv
WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://kb.example.com
WEB_COOKIE_SECURE=true
WEB_HOST=127.0.0.1
WEB_PORT=8000
WEB_SERVE_STATIC=true
WEB_STATIC_DIR=web/dist
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
```

在 release 工作目录启动：

```bash
.venv/bin/python -m app.cli web-server
```

如果需要让同一个 ASGI 进程同时承载 `/mcp`，使用生产组合提供的 combined
`mcp-server --transport streamable-http`，不要额外并行启动第二个 public Web/MCP
实例。具体 systemd/Caddy 单元见 [production runbooks](../operations/production/README.md)。

## 3. Split 部署

把 `web/dist` 交给 Nginx/Caddy/其他静态服务器，并让 Python 只提供 API：

```dotenv
WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://kb.example.com
WEB_COOKIE_SECURE=true
WEB_HOST=127.0.0.1
WEB_PORT=8000
WEB_SERVE_STATIC=false
WEB_STATIC_DIR=web/dist
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
```

反向代理需满足：

- `/api/v1/*` 转发到 `127.0.0.1:8000`，保留方法、query、body、Origin、
  `Sec-Fetch-Site`、Cookie、`X-CSRF-Token` 和 `Set-Cookie`；
- `/login`、`/library`、`/videos/<public-id>` 等前端路由回退到 `index.html`；
- 未知 `/api/*` 路径继续返回后端 JSON 404；
- HTML/API 不缓存，指纹化 `/assets/*` 可 immutable 缓存；
- 应用的 CSP、HSTS、`nosniff`、frame blocking、Referrer-Policy 和 Permissions-Policy
  同样覆盖静态 HTML/asset；`connect-src` 保持 `'self'`；
- 不代理 loopback Channel Gateway。

可直接参考仓库中的 [Nginx 模板](../../deploy/nginx/notebook-agent-web.conf) 和
[安全 header 模板](../../deploy/nginx/notebook-agent-web-security-headers.conf)，
但上线前要替换域名、证书路径和 service account，且先运行 `nginx -t`/等价完整配置
验证。

## 4. 验证浏览器安全契约

从最终 public origin 验证：

```text
GET /                      -> SPA index
GET /login                 -> SPA index
GET /library               -> SPA index
GET /videos/<public-id>    -> SPA index
GET /api/v1/health         -> 200 JSON
GET /api/v1/capabilities   -> 200 JSON
GET /api/v1/does-not-exist -> JSON 404
```

再完成真实登录，确认浏览器只收到 `__Host-kb_session` 和 `__Host-kb_csrf`，请求
使用相对 `/api/v1/*` URL，并通过 exact Origin + `X-CSRF-Token` 完成一次写操作。
不要把 API token 放到 browser storage、domain cookie 或跨 origin header。

需要在 Debian/Ubuntu 主机上用 Nginx 与 systemd 进行原子 release 切换、migration
准入和 paired rollback 时，继续阅读
[Debian/Ubuntu + Nginx 生产手册](../operations/production/debian-nginx-frontend.md)。
