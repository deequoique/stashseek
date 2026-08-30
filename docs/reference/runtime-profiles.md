# 运行模式参考

`./scripts/stashseek` 是单机运行时的生命周期入口。它负责把选定的
应用进程、迁移和本地 Compose 依赖作为一个运行单元管理；它不是 secret
manager，也不会替你配置主机、TLS 或公网 DNS。

## 模式矩阵

| 模式 | 应用组件 | 典型用途 | 必要外部依赖 |
| --- | --- | --- | --- |
| `read` | Streamable HTTP MCP | 只读问答、资料库浏览和 MCP discovery | PostgreSQL；embedding/model 由实际问答需要 |
| `full` | MCP、loopback Channel Gateway、一个 Celery worker、一个 Celery Beat | 保存 URL、后台导入、条目管理和可选渠道 | PostgreSQL、Redis、MinIO、`CHANNEL_GATEWAY_SECRET` |
| `langbot` | loopback Channel Gateway、一个 Celery worker、一个 Celery Beat | 仅运行后台任务和可选 LangBot 渠道 | PostgreSQL、Redis、MinIO、`CHANNEL_GATEWAY_SECRET` |

`read` 不应启动 Redis、MinIO、worker 或 Beat。`langbot` 默认不启动公共
MCP endpoint。`full` 和 `langbot` 中 worker 与 Beat 是两个独立 OS 进程；
一个 supervisor 只允许一个 Beat。

是否由 launcher 启动 PostgreSQL、Redis、MinIO，取决于配置目标：本地
loopback 依赖可以进入它拥有的 Compose project；`DATABASE_URL`、远程
`REDIS_URL` 或远程 `MINIO_ENDPOINT_URL` 会把对应服务视为外部依赖。外部
依赖只做 readiness 检查，launcher 不会停止或修改它们；外部 object store
的 bucket 检查是只读的。

## 监听器和组件

| 组件 | 默认监听 | 启动命令 |
| --- | --- | --- |
| MCP | `MCP_HOST=127.0.0.1`、`MCP_PORT=8000` | `python -m app.cli mcp-server --transport streamable-http` |
| Channel Gateway | `CHANNEL_GATEWAY_HOST=127.0.0.1`、`CHANNEL_GATEWAY_PORT=8765` | `python -m app.cli gateway-server` |
| Web（独立模式） | `WEB_HOST=127.0.0.1`、`WEB_PORT=8000` | `python -m app.cli web-server` |
| Celery worker | 无 HTTP listener | `python -m celery -A app.ingest.tasks.celery_app worker --loglevel=INFO --queues=ingest,maintenance` |
| Celery Beat | 无 HTTP listener | `python -m celery -A app.ingest.tasks.celery_app beat --loglevel=INFO` |

同一 profile 中的 MCP 和 Gateway 必须使用不同端口。Web 与 MCP 也应使用
不同端口；生产的 combined ASGI 形态可以由 MCP 进程同时分派 Web、API 和
MCP 路径。

## 生命周期

```text
init → prepare dependencies → one Alembic head → start children
                                              ↓
                                         readiness gates
                                              ↓
                                            running
```

启动器在应用子进程前执行依赖检查和单一 Alembic head 检查，随后对所选的每个
listener 做 bounded readiness 检查。运行中的子进程退出时，所属 runtime 会
停止；`status` 会显示进程、依赖、Compose 和 listener 状态。一次性 status
检查若发现当前环境与 supervisor 捕获的非 secret service target 不一致，
会报告配置不匹配，而不会探测另一个数据库、broker 或 object store。

`stop` 和 `restart` 只操作 launcher 记录且经过进程身份校验的 supervisor
及其子进程。它们不按进程名、端口或通配符杀死其他服务；外部数据库、Redis
和 MinIO 不在停止范围内。

## launcher 生成的配置

`init` 只生成被忽略的 `.env.runtime`，权限应为 `0600`，并只写入非默认的
profile 选择和必要 secret，不会复制完整 `.env.example`。配置优先级为：

```text
进程环境 > 项目根 .env > launcher .env.runtime > app 默认值
```

launcher 不覆盖用户维护的 `.env`，不把 `MIGRATION_DATABASE_URL` 传给长期
运行的 worker、MCP 或 gateway，也不在状态和日志中打印 secret。Neon 等 pooled
runtime URL 需要同 host family/database 的 direct
`MIGRATION_DATABASE_URL`，仅供一次性迁移使用。

## 安全前提

- 默认拒绝 MCP 的非 loopback 绑定。只有已经配置 TLS reverse proxy 并明确
  接受边界时，才设置 `STASHSEEK_ALLOW_NON_LOOPBACK=true`。
- Gateway 必须始终绑定 loopback，并使用至少 32 个字符的
  `CHANNEL_GATEWAY_SECRET`。
- `MCP_PATH` 必须是非根绝对路径，不能带 query、fragment 或末尾 `/`。
- 生产 Web 必须开启 `WEB_AUTH_ENABLED`、使用 HTTPS `WEB_PUBLIC_ORIGIN`、
  `WEB_COOKIE_SECURE=true`，并让 Redis、MinIO 的发布端口仅绑定 loopback。
- 自定义 CA bundle 在启动基础设施和应用子进程前验证；健康快照和状态输出
  必须脱敏且有大小上限。

## 直接运行与 launcher 的边界

`app.cli`、Celery 和 Docker Compose 仍可直接使用，适合已有 process manager
或测试 harness 的高级 operator。直接运行不会自动获得 launcher 的 reservation、
profile 组合、迁移门禁、子进程归属和安全停止语义；需要完整单机生命周期时，
应以本页定义的 launcher 行为为准。
