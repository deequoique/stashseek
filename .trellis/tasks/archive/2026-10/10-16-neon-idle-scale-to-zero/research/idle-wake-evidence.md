# 空闲唤醒证据（2026-10-16）

## Neon 控制台观察

- 项目 `notebook-agent`，分支 `main`。Monitoring 的 Last hour 视图（15:31–16:23）：
  RAM/CPU 每约 10 分钟出现一段约 5 分钟的 allocated 块，块与块之间是
  endpoint inactive。按截图坐标测得块间距约 9.97 分钟，块宽约 5 分钟。
- 计算配置：min 0.25 CU，max 8 CU，scale to zero 5 分钟（默认值）。
- 账单（Launch 计划，2026-10-01 至 10-16）：Compute 38.37 CU-h 共 $4.02，
  约合 $0.105/CU-h；Storage 0.53 GB-month 共 $0.19。

## 与代码的对应关系

- 10 分钟周期：`deliver-pending-ingest-notifications` 的 Beat 调度，
  `INGEST_NOTIFICATION_INTERVAL_SECONDS` 默认 600（`app/ingest/tasks.py`
  中 `_notification_interval_from_env`、`celery_app.conf.beat_schedule`）。
  每个 tick 都会调用 `IngestNotificationPoller.sweep_once()`，无条件执行：
  读 PostgreSQL 时间、跑外连接候选扫描，再为心跳跑一次 backlog 年龄查询。
- 5 分钟块宽：Neon 的挂起超时。每个 tick 唤醒计算节点后，节点要空闲 300 秒才挂起。
- `purge-expired-items` 每 3600 秒执行一次（`TRASH_PURGE_INTERVAL_SECONDS`），
  每次都会额外碰一次数据库。
- 结果：空闲时计算节点约 50% 的时间处于醒着状态，约 3 CU-h/天，按每月计约 $9.5。
  10-02 的修复（10 秒改为 600 秒，10-07 经 PR #25 上线）把醒着的比例从
  100% 降到约 50%，没有降到 0。

## 已排除

- 本机进程（15:16 起运行的 worker 和 web-server）连接的是 `ep-falling-fog…` 测试分支，
  而且检查时没有到 5432 的连接，本机也没有 Beat 进程。
- 应用代码里没有其他周期性访问数据库的循环。`mcp_readiness` 的 `SELECT 1`
  只在被请求时执行。

## 隐患

- 本地 `.env` 第 51 行仍是 `INGEST_NOTIFICATION_INTERVAL_SECONDS=10`，
  而且 `.env` 的 `DATABASE_URL` 指向生产 main。本地只要用 `--profile full`
  启动一次（会带上 Beat），就会每 10 秒查询生产库，让它一直醒着。

## 尚未解释

- 生产 VPS 在 2026-09-10 至 2026-10-06 处于过期停机状态，但 10-01 至 10-06
  仍有计算计费。可能来源是那几天本地开发或 pytest 直连了生产库。
  需要用 Neon consumption API 拿到按天数据才能确认（Neon MCP 尚未授权）。
  本任务不处理这一项。

## 相关基础设施事实

- 生产 Redis：`redis:7-alpine`，开启 `--appendonly yes --appendfsync always`，
  使用持久卷（`deploy/compose/production-dependencies.yml`）。Redis 中的
  修复索引在重启后可以保留。
- Redis 键命名惯例：`notebook-agent:<area>:v1`（参见 `app/web_auth.py`）。
- 通知投递账本 `IngestCompletionDelivery` 的字段：`status`、`disposition`、
  `claim_token`、`claimed_at`、`attempts`、`next_attempt_at`、`completed_at`。
  可重试的失败按指数退避，由 `ingest_notification_retry_base_seconds` 和
  `ingest_notification_retry_max_seconds` 控制。
