# Neon 生产环境持续查询与成本优化

## Goal

降低生产和本地误连生产 Neon 时的无效数据库访问，避免空闲计算实例被后台轮询持续唤醒，同时保留摄入完成通知的可靠投递和幂等性。

## Confirmed Facts

- `app.ingest.tasks` schedules `deliver_pending_ingest_notifications_task` every `INGEST_NOTIFICATION_INTERVAL_SECONDS`; the code default is 10 seconds.
- `IngestNotificationPoller.sweep_once()` opens a database session on every tick. An empty tick still reads PostgreSQL time, scans the eligible completion-event join, and performs a backlog observation query for the heartbeat.
- The production systemd Beat unit intentionally runs this schedule, and production environment templates do not override the 10-second default.
- `app.deployment` falls back to the `full` profile when no profile is specified. `full` starts both worker and Beat, so an unattended local process using the pooled Neon URL can keep Neon active.
- A read-only production connection attempt from this workspace reached the Neon endpoint but the server closed the connection before authentication/query execution. No production query statistics were collected, and no write, extension creation, statistics reset, or configuration change was attempted.

## Requirements

1. Normal completion notifications must be event-triggered through the existing Celery/Redis runtime after the durable completion event commits, so a successful ingestion does not wait for a database poll tick.
2. Event-triggered delivery must claim a specific completion event through the existing delivery ledger and remain idempotent under duplicate task delivery, stale claims, retries, and broker failure.
3. Keep a bounded repair sweep for events whose enqueue step failed, but raise its default interval to 10 minutes so the repair loop is not a continuous Neon keepalive. The interval and existing duration/claim limits remain configurable.
4. Change the launcher default profile to `read`; starting `full` or `langbot` must remain explicit for local ingestion/channel work.
5. Update production configuration documentation and tests so the new notification path, fallback interval, and safe launcher default are explicit and regression-tested.
6. Do not modify Neon data, reset `pg_stat_statements`, create extensions, change endpoint autoscaling, or delete any user data as part of this task.

## Acceptance Criteria

- [ ] A committed completion event causes one targeted notification task to be enqueued without a periodic database scan, and delivery remains exactly-once at the ledger side under duplicate task execution.
- [ ] The targeted task performs only event-scoped database work; the repair sweep remains bounded and handles enqueue/broker failures.
- [ ] The default repair schedule is 600 seconds, its max-duration validation still fails closed, and all existing explicit interval overrides continue to work.
- [ ] `stashseek init`/`start` with no profile selects `read` and starts no worker or Beat; explicit `full`/`langbot` behavior is unchanged.
- [ ] Focused notification, deployment, and configuration tests pass; syntax/quality checks pass for all changed packages.
- [ ] Production rollout instructions identify the required environment/default change and a post-deploy verification using read-only Neon metrics, without including credentials.

## Out of Scope

- Replacing Celery/Redis, changing the notification message contract, redesigning the completion ledger schema, or changing user-visible notification text.
- Direct changes to the live production host or Neon project from this workspace; deployment remains a separately authorized rollout step.
