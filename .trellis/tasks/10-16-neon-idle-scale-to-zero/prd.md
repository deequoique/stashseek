# 空闲时让 Neon 归零：通知修复扫描 Redis 门控与每日清理

## Goal

没有用户流量时，生产后台任务不得唤醒 Neon 计算节点，让 scale-to-zero 真正生效；
同时保持来源渠道完成通知的可靠投递和幂等性。

本任务接续 `10-02-neon-production-query-cost`。那次修复把通知修复扫描从 10 秒改为
600 秒，空闲时醒着的比例从 100% 降到约 50%，但没有降到 0。证据见
`research/idle-wake-evidence.md`。

## Confirmed Facts

- 生产 Beat 每 600 秒执行一次 `deliver_pending_ingest_notifications_task`。
  即使没有待办，每个 tick 也会执行若干条 SQL，而 Neon 要空闲 300 秒才挂起，
  因此空闲时每 10 分钟就有 5 分钟处于醒着状态，约 3 CU-h/天。
- `purge_expired_items_task` 每 3600 秒执行一次，每次都查询数据库。
- 正常的通知路径由事件触发：完成事件提交后，系统会立即入队
  `deliver_ingest_notification_task(event_id)`。周期扫描只负责兜底：
  入队失败、可重试的投递失败、worker 崩溃后留下的过期认领，以及历史积压。
- 生产 Redis 开启了 AOF，并设置 `appendfsync=always`。
- 本地 `.env` 设置了 `INGEST_NOTIFICATION_INTERVAL_SECONDS=10`，并且
  `DATABASE_URL` 指向生产库。

## Requirements

1. **修复扫描使用 Redis 门控。** 周期修复扫描先读取 Redis 中的修复索引。
   只有在索引里有已到期的事件、索引基线缺失或 Redis 不可读时，才访问 PostgreSQL。
   其他情况下，空闲 tick 对 PostgreSQL 的查询数必须为零，包括心跳。
2. **修复索引必须覆盖所有兜底场景。** 下列情况都要让对应事件在合适的时间进入
   “到期”状态：完成事件提交后，定向任务尚未完成（允许它崩溃或卡在认领中）；
   入队失败；可重试的投递失败（到期时间等于 `next_attempt_at`）；
   被释放的延期认领；运维执行 redrive。事件进入终态（成功、跳过、终态失败、
   重试耗尽）后，从索引中移除。
3. **每日全量兜底。** 保留一次不经门控的全量修复扫描，默认每 86400 秒一次，可以配置。
   它负责处理 Redis 写入失败时漏记的事件，并根据数据库状态重建修复索引。
   最坏情况下，Redis 不可用期间完成的事件，其通知最多延迟 24 小时。
4. **回收站清理改为每天一次。** `TRASH_PURGE_INTERVAL_SECONDS` 的默认值从 3600
   改为 86400。代码默认值、任务回退值、`.env.example` 和文档必须保持一致。
   每日全量修复扫描与每日清理默认使用相同的间隔，空闲时每天只唤醒数据库约一次。
5. **失败时放行，不能因门控丢通知。** Redis 读取失败时，退回到原有的数据库扫描。
   Redis 写入失败只发出隐私安全的诊断，不影响摄入结果和投递状态，由每日全量扫描兜底。
6. **可观测性。** 空闲跳过的 tick 仍然发出 `notification_poller_heartbeat`，
   带上新的数值字段（例如 `idle_skipped=1`），且不执行 backlog 查询。
   诊断事件只包含固定阶段名、计数、内部事件 ID 和稳定错误码。
7. **消除本地 10 秒隐患。** 把本地 `.env` 的 `INGEST_NOTIFICATION_INTERVAL_SECONDS`
   改为 600。`.env` 不入库，这一项只修改本机文件。
8. **规范与文档同步。** 更新 `database-guidelines.md`（Neon 空闲约束）、
   `ingest-completion-queue.md`（修复扫描与索引契约）和
   `docs/reference/configuration.md`，并写入新的门控行为和默认值。

## Acceptance Criteria

- [ ] 索引基线存在且没有到期事件时，修复扫描 tick 对 PostgreSQL 发出零个会话或语句（测试断言 session factory 未被调用），并发出 `idle_skipped=1` 心跳。
- [ ] 一个完成事件提交后，即使定向任务从未执行或在认领后崩溃，也会在认领超时之后的第一个 tick 被修复扫描投递。
- [ ] 可重试失败在 `next_attempt_at` 之前不会触发数据库访问，到期后的第一个 tick 会访问数据库。
- [ ] 事件到达终态后从索引中移除。终态之后不再有 tick 访问数据库（基线存在时）。
- [ ] Redis 读取异常时扫描退回数据库路径，Redis 写入异常不改变摄入或投递结果。
- [ ] 基线缺失时（全新或数据丢失的 Redis），执行一次全量扫描，然后写入基线。
- [ ] 每日全量扫描不经门控，并根据数据库中的非终态投递重建索引。
- [ ] `TRASH_PURGE_INTERVAL_SECONDS` 默认值为 86400，现有的显式覆盖和校验仍然生效。
- [ ] 现有通知、完成事件和清理相关测试全部通过；新增测试覆盖上述场景；变更包的 lint 和语法检查通过。
- [ ] 本地 `.env` 不再出现 10 秒间隔。
- [ ] 上线说明写明：需要检查生产 env 文件中旧的显式覆盖；上线后用 Neon Monitoring 的 Last day 视图验证空闲时段持续挂起。

## Out of Scope

- 修改 Neon 的挂起超时、autoscaling 或计划。
- 为 pytest 直连生产库增加 conftest 防护（另行立项）。
- 排查 10-01 至 10-06 的计费来源。
- 修改通知文案、投递账本表结构，或用其他组件替换 Celery/Redis。
- 给回收站清理加 Redis 门控（改为每天一次已足够）。
- 直接修改生产主机或 Neon 项目。上线仍走 dev → main → Production 审批门。
