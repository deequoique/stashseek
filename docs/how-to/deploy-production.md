# 部署通用生产环境

这是一份与云厂商无关的生产上线配方。它说明边界、顺序和验收条件；OVH/Caddy、
仅限 Telegram 的 LangBot 等主机专属命令仍放在[特定生产环境手册](../operations/production/README.md)。

## 目标拓扑

推荐的同机边界如下：

```text
浏览器 / MCP client
        │ HTTPS
        ▼
 Caddy/Nginx（只公开 80/443）
        │
        └── 127.0.0.1:8800  combined mcp-server（Web + API + MCP）

 LangBot plugin ── 127.0.0.1:8765 Channel Gateway（不经过反向代理）
 worker / beat ── Redis（loopback）── MinIO（loopback）
          └────── PostgreSQL（外部或私有）
```

combined runtime 不应再额外启动一个 public `web-server`；否则 Web、API 和 MCP 会
出现重复入口和不一致的安全边界。需要独立静态前端时，按[部署独立前端](deploy-frontend.md)
选择 split topology。

## 1. 准备主机和 secret

为应用创建专用系统用户、发布目录、日志目录和 root-owned secret 文件。secret 文件
建议 `0600`（若 systemd 需要 service group 读取，使用 root-owned `0640` 并限制该组）。
至少准备：

```dotenv
STASHSEEK_ENV=production
DATABASE_URL=<pooled-or-runtime-postgres-url>
MIGRATION_DATABASE_URL=<matching-direct-migration-url>
ZHIPU_API_KEY=<embedding-provider-key>
AGENT_MODEL=openai:gpt-5-mini
AGENT_API_KEY=<agent-provider-key>
WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://kb.example.com
WEB_COOKIE_SECURE=true
WEB_HOST=127.0.0.1
# WEB_PORT applies only to a standalone `web-server`; combined runtime uses MCP_PORT.
WEB_PORT=8000
MCP_HOST=127.0.0.1
MCP_PORT=8800
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=false
CHANNEL_GATEWAY_SECRET=<at-least-32-random-characters>
CHANNEL_GATEWAY_HOST=127.0.0.1
CHANNEL_GATEWAY_PORT=8765
STASHSEEK_LOG_DIR=/var/log/notebook-agent
```

生产 Web auth 还必须设置显式 `EMAIL_PROVIDER` 及其邮件凭据。`MIGRATION_DATABASE_URL`
只给一次性 migration 进程，不能传给 worker、Web、MCP 或 gateway。Neon/其他 pooler
运行时 URL 必须与 direct migration URL 指向同一个 host family 和 database，并使用 TLS。

如果使用本地依赖，Redis/MinIO 的 published ports 只允许 loopback，例如
`127.0.0.1:16379:6379`、`127.0.0.1:19000:9000` 和 `127.0.0.1:19001:9001`；生产
Compose 不应启动 PostgreSQL。不要复用 Web auth secret、gateway secret、MCP token
和 extension capture token。

## 2. 构建并固定一个 release

每次发布让 Python 环境、前端 `web/dist` 和 schema 绑定同一个 commit。至少完成：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build
```

保留 release 的 commit、Alembic schema head、`web/dist` 和 app entrypoint 清单；不要
在多个 release 之间复用可变 `.venv` 或覆盖 active release。

如果使用仓库内的受限生产入口，部署请求必须是当前 `origin/main` 的完整 40 位小写
SHA：

```bash
deploy <40-lowercase-hex-sha>
```

脚本会拒绝格式不符、不是当前 `origin/main` 或已有 deployment lock 的请求。

## 3. 以依赖和迁移为上线闸门

上线前先停止新的 Web/渠道写入，并确认：

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic check
```

必须只有一个 head，`check` 不应报告新的 upgrade operation。Redis 必须有持久化写入
保证，MinIO 必须同时通过 liveness、readiness 和目标 bucket admission；只返回 HTTP
200 的 liveness 不能视为 object store ready。worker readiness 要同时满足：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app inspect ping
.venv/bin/celery -A app.ingest.tasks.celery_app inspect active_queues
```

至少一个 worker 返回 `pong` 并监听 `ingest`、`maintenance`，且只能有一个 Beat。
不要在 provider 或外部 PostgreSQL 仍未 ready 时启动应用流量。

## 4. 按所有权启动服务

推荐顺序是：

1. 外部 PostgreSQL、Redis、MinIO 通过 bounded readiness；
2. 一次性 migration 成功；
3. 一个 worker（`ingest,maintenance`）和一个 Beat；
4. combined `mcp-server --transport streamable-http`；
5. loopback Channel Gateway；
6. 如启用 LangBot，等待 required bridge plugin `initialized` 后再启用 adapter。

应用子进程退出应使其所属 runtime 进入失败状态；不要让 supervisor 在深度 provider
探测上无限等待。启动器的生命周期命令只停止它记录且确认属于自身的进程；不要用
按名字杀进程、按端口批量终止或 `docker compose down` 影响同机其他服务。

## 5. 配置 TLS 和反向代理

反向代理只转发公开 Web/MCP origin：

- 公开端口只有 80/443；
- `/mcp` 及 `/api/v1/*` 保留方法、body、Origin、Cookie、CSRF header 和响应 header；
- gateway、Redis、MinIO、Web/MCP loopback 端口不公开；
- URL capability path（若启用）不写入 access log，必须使用 HTTPS；
- 保留 MCP SDK 的 DNS rebinding/Host 校验，不信任任意 forwarded host；
- split 前端时，未知 `/api/*` 返回 JSON 404，不得回退成 SPA HTML。

修改代理前先验证完整候选配置，再优雅 reload；如果新 route 破坏同机旧 route，
只回滚本次 StashSeek Chat site block。不要通过 wildcard CORS、domain cookie 或
浏览器 localStorage token 绕过同源模型。

## 6. 上线 smoke

应用和代理都已启动后，从最终 public origin 检查：

```text
GET /                      -> SPA index
GET /login                 -> SPA index
GET /library               -> SPA index
GET /api/v1/health         -> 200 JSON
GET /api/v1/capabilities   -> 200 JSON
GET /api/v1/does-not-exist -> JSON 404，而不是 SPA HTML
POST /mcp                  -> 使用有效 grant 完成 initialize
```

再完成一遍真实登录、资料库读取、一个 CSRF 保护的写操作和一个带时间戳引用的问答。
最后确认公网只能访问 80/443，`8765`、Redis、MinIO 和应用 loopback 端口不可从外部
连接。将这些检查结果与 release SHA、schema head 一起保存，但不要保存 token、cookie、
问题正文或字幕内容。
