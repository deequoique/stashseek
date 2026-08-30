# 升级与回滚

目标是让 Python、前端和 schema 以可审计的 release 一起演进。生产回滚优先回滚
应用 binary 和 `web/dist`，保留向前兼容的数据库 schema；不要把 migration downgrade
当作普通发布撤销按钮。

## 1. 发布前冻结和备份

1. 停止 LangBot adapters/plugin，阻止新渠道消息。
2. 在反向代理隔离 Web/MCP 写入口，等待或记录 active ingestion。
3. 按[备份与恢复](back-up-and-restore.md)备份 PostgreSQL、object store 和 LangBot 配置。
4. 记录当前 release、schema head、worker/Beat 状态和上一份已知良好 release。

不要在升级前删除 Redis queue、`ingest_dispatch`、completion event/ledger、conversation
history 或回收站数据。

## 2. 构建新 release

为新 commit 创建独立工作目录和虚拟环境，运行：

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

保留 release manifest，至少记录 commit SHA、预期 schema head、`web/dist` 和入口命令。
不要在 active release 上原地覆盖依赖或前端构建产物。

## 3. 运行迁移并启动兼容进程

确认新 release 能连接目标数据库后，以一次性 migration 进程执行：

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic check
```

`MIGRATION_DATABASE_URL` 只注入该进程；不要把它传给长期运行的 app/worker。然后按
以下顺序启动：

1. Redis、MinIO 和 bucket admission；
2. worker（监听 `ingest,maintenance`）和单一 Beat；
3. combined runtime 或 API-only Web；
4. loopback gateway；
5. 通过 required-plugin readiness 后启动 LangBot adapters。

检查 `/api/v1/health`、`/api/v1/capabilities`、SPA 路由、未知 API JSON 404、MCP
`initialize/tools/list/tools/call`、资料库读写和一个时间戳引用。完成前不要重新开放
写入或删除上一份 release。

## 4. 本地 launcher 的升级

单机 launcher 管理的环境可以在备份后切换 profile 并重启：

```bash
./scripts/stashseek status
./scripts/stashseek restart --profile full
./scripts/stashseek status
```

如果 `restart` 报 migration、端口或依赖错误，保留现场并按[排查问题](troubleshoot.md)
处理；不要删除 `.runtime/`、Compose volume 或 `.env.runtime` 以“重置”。

## 5. 生产回滚应用和前端

当新 release 的健康检查失败时：

1. 隔离 public 写入口并停止新 release 的 owned units；
2. 选择上一份已知良好的 release manifest，确认它仍存在且不是被删除的 active target；
3. 将应用和 `web/dist` 一起切回同一个 release；
4. 重新启动 worker、Beat、应用和 gateway，按同样的 smoke 顺序验证；
5. 验证数据库 live schema 与旧 release 的兼容性后，才恢复渠道和 Web 写入。

仓库内生产部署脚本会锁定部署、保留 previous release，并在 dependency/migration/
health/gateway/LangBot readiness 失败时尝试恢复 previous release。执行前请阅读
[生产 runbook](../operations/production/ovh-caddy.md) 的主机边界；不要手工 `docker compose down`
或重启同机不属于 StashSeek Chat 的服务。

## 6. 不要自动 downgrade

如果新 migration 已经改变生产 schema，应用 binary 可以在向前兼容的前提下回滚，
但不得直接运行 destructive `alembic downgrade`。只有在隔离恢复演练确认安全、已有
完整 PostgreSQL/MinIO 备份、停止 gateway/Web/Beat 且管理员批准后，才能执行指定
revision 的数据恢复流程。回滚不得重置 Telegram/微信身份、对话历史、已有内容或
对象存储。
