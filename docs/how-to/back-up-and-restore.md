# 备份与恢复

StashSeek Chat 的可恢复状态不只在 PostgreSQL。一次可用的备份至少包括数据库、
原始字幕/文本对象、LangBot 的外部配置和 secret manager 中的凭据清单。

## 1. 先冻结写入并记录版本

备份前停止新 Web/MCP/LangBot 写入，或在反向代理临时隔离写入口；让已经运行的 ingestion
完成，或记录仍处于 `pending/enqueued/running` 的 dispatch。记录：

- 当前应用 commit/release；
- `alembic current` 的 schema head；
- `MINIO_BUCKET` 和 object-store endpoint（不要记录 secret）；
- worker/Beat 状态和未完成任务数量。

不要为了备份删除队列、清空 Redis、重跑 ingestion 或修改 tenant/identity。

## 2. 备份 PostgreSQL

本地 Compose PostgreSQL 可以使用 custom format：

```bash
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' \
  > kb-$(date +%Y%m%d-%H%M%S).dump
```

外部 PostgreSQL 使用平台的加密 snapshot 或 `pg_dump --format=custom`，并让管理员
确认连接 URL 使用 direct/受支持的 TLS 配置。备份文件包含用户、渠道身份、知识条目、
字幕索引、embedding、对话历史和 pending action，必须加密、限权并设置保留期。

## 3. 备份 object store 和外部配置

PostgreSQL dump 不包含 MinIO/S3 中的原始字幕或文本对象。使用基础设施快照或团队
批准的 S3-compatible backup 工具，对 `MINIO_BUCKET` 做版本化/加密备份；至少记录
bucket 名称、对象版本策略和快照时间。不要把 presigned URL、access key 或 secret key
写入备份清单。

单独备份但不把 secret 原文放进普通压缩包：

- `/etc/notebook-agent/` 中受限的环境文件清单；
- LangBot 自己的数据库、plugin 配置和平台登录状态；
- Caddy/Nginx/systemd 配置版本；
- secret manager 中的 key 名称、轮换记录和恢复责任人。

## 4. 在隔离环境演练恢复

1. 准备与目标版本兼容的 PostgreSQL、Redis、MinIO 和空的应用工作区。
2. 恢复 object store bucket 和对象，再恢复 PostgreSQL custom dump；不要让生产渠道
   在恢复过程中写入。
3. 以恢复的 release 启动一次性 migration 检查：

   ```bash
   .venv/bin/alembic current
   .venv/bin/alembic check
   ```

4. 启动 worker（`ingest,maintenance`）、单一 Beat、应用和 gateway，检查 object
   store bucket admission 与 Celery readiness。
5. 用非生产账号完成 Web 登录、资料库读取、时间戳 transcript 和带依据问答 smoke；
   再检查 queued/processing dispatch 是否按预期恢复或可安全重试。

恢复演练必须在隔离租户执行，不能用真实 session cookie、MCP token 或渠道消息做测试。

## 5. 生产恢复顺序

发生数据或主机故障时：

1. 先阻断 public Web、MCP 和 LangBot 写入，保留故障现场和日志摘要；
2. 固定要恢复的 release 与数据库/object snapshot 时间点；
3. 恢复 object store 和 PostgreSQL；
4. 运行 `alembic current/check`，确认只有一个 head；
5. 启动依赖、worker、Beat、应用和 gateway，按[通用生产部署验收](deploy-production.md)
   验证；
6. 最后恢复登录渠道和流量，先提交一条无敏感内容的测试视频并验证 ready/citation。

不要用“应用版本回滚”冒充“数据恢复”。身份归并提交后不能通过用户命令拆分；代码
回滚会保留归并后的 tenant 和内容。若确实需要恢复到更早数据，只能走已审批、备份
验证过的管理员恢复流程。
