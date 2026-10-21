# Technical Design

## 边界

- PostgreSQL 仍是唯一的事实来源：完成事件表和投递账本
  （`ingest_completion_event` / `ingest_completion_delivery`）。
- Redis 只新增一个**可以丢失的修复索引**，用来回答“现在需不需要去数据库看一眼”。
  索引丢失或写入失败，最多让一次通知延迟到下一次每日全量扫描，不会造成重复投递或漏投。
  幂等性仍由账本的认领令牌保证。
- 消息文案、账本表结构、定向任务 `deliver_ingest_notification_task(event_id)` 的契约都不变。

## Redis 修复索引

新模块 `app/ingest/notification_repair_index.py`：

```text
due key:      notebook-agent:ingest-notification-repair:v1:due       (ZSET)
              member = str(event_id)，score = 整数 epoch 秒，表示“最早在这个时间需要修复扫描去看它”
baseline key: notebook-agent:ingest-notification-repair:v1:baseline  (STRING，无 TTL)
              只有在一次全量扫描并重建索引之后才写入
```

接口（Protocol，由 Redis 实现和测试用的内存实现共同遵守）：

| 方法 | 语义 |
| --- | --- |
| `mark_due(event_id, at)` | `ZADD ... LT`：成员不存在时新增；已存在时只允许把时间提前（以最早的为准）。 |
| `has_due(now) -> bool` | `ZRANGEBYSCORE due -inf now LIMIT 0 1` |
| `due_members(now, limit) -> list[(event_id, score)]` | 有界读取已到期的成员 |
| `members(limit)` | 全量重建时读取所有成员（索引很小，有上限） |
| `reconcile(event_id, expected_score, new_score \| None)` | Lua 比较后设置：只有当前 score 仍等于 `expected_score` 时，才改为 `new_score`；传 `None` 时删除该成员。用来防止和并发的 `mark_due`（例如 redrive）发生竞态。 |
| `has_baseline()` / `set_baseline()` | 基线标记 |

- Redis 客户端由 `settings.redis_url` 构造，`socket_connect_timeout` 和
  `socket_timeout` 都不超过 0.5 秒（参照 `mcp_readiness`、`web_auth` 的写法）。
  任何 Redis 异常都向上抛出，由调用方决定放行还是记录诊断。
- 生产 Redis 7 支持 `ZADD LT` 和 Lua 脚本。

## 到期时间如何从数据库状态推导

`repair_due_at(event_exists, delivery_row, db_now, settings) -> int | None`
是唯一的推导函数，返回 `None` 表示终态，应从索引中移除：

| 数据库状态 | due_at |
| --- | --- |
| 事件已删除或不存在 | `None` |
| 没有投递行（定向任务还没认领） | `db_now` |
| `status=claimed` | `claimed_at + ingest_notification_claim_timeout_seconds`（过期认领可以被回收的时间） |
| `status=failed` 且 `disposition IS NULL`（可重试） | `next_attempt_at`，为空时取 `db_now` |
| `status=failed` 且 `disposition IN (terminal_failure, retry_exhausted)` | `None` |
| `status=succeeded` | `None` |

## 写入点

1. **完成事件提交后**（`_complete_dispatch` / `_mark_dispatch_failed` 的
   post-commit 段，在入队之前）：执行
   `mark_due(event_id, time.time() + claim_timeout)`。正常情况下，定向任务会在几秒内
   投递成功并把它移除，不会多出一次数据库唤醒。如果定向任务丢失或在认领后崩溃，
   认领过期后修复扫描会接手。
2. **入队失败**（`_enqueue_ingest_notification_best_effort` 的 except 分支）：
   执行 `mark_due(event_id, now)`。
3. **定向投递结束后**（`deliver_event`）：数据库此时本来就醒着，用一次按 `event_id`
   的小查询读取投递行，再用 `reconcile` 按上表更新或移除。认领返回 `None`
   （被别人持有或已是终态）时同样执行 reconcile。
4. **redrive**（`redrive_failed` 成功之后）：执行 `mark_due(event_id, now)`。
5. 以上写入全部是尽力而为：Redis 异常只发出诊断
   `notification_repair_index_write_failed`，不改变摄入结果或投递结果。

## 修复扫描的三种模式

```text
gated tick（Beat，每 INGEST_NOTIFICATION_INTERVAL_SECONDS，默认 600）
  ├─ Redis 读取异常           → 放行：执行原有 sweep_once()，诊断 repair_gate_unavailable=1
  ├─ 基线缺失                 → 按全量模式执行
  ├─ 没有到期成员             → 不访问 PostgreSQL；发出心跳 heartbeat=1, idle_skipped=1，计数为 0
  └─ 有到期成员               → sweep_once()，然后对本轮认领的事件和到期成员执行 reconcile

full repair（Beat，每 INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS，默认 86400；基线缺失时也会触发）
  → sweep_once()（不经门控）
  → 重建索引：对所有非终态投递行，以及没有投递行的可投递事件（有上限，例如 1000，超出时记诊断），
    用推导出的 due_at 执行 mark_due；
    对索引中已经是终态或已删除的成员执行 reconcile(None)
  → set_baseline()

legacy（未注入 repair_index，例如测试或回滚）
  → 与当前行为完全一致：每个 tick 都执行 sweep_once()
```

- 新增任务 `app.ingest.tasks.repair_all_ingest_notifications_task`，路由到
  `maintenance` 队列，负责全量模式。保留 `deliver_pending_ingest_notifications_task`
  这个任务名，改为门控模式，这样回滚到旧代码时 Beat 的条目仍然兼容。
- 空闲 tick 的心跳省略 `oldest_eligible_backlog_age_seconds` 字段，因为没有查询数据库。
  原来“backlog 为空时取 0”的约定只适用于真正执行了查询的 tick。
- 门控 tick 中，如果某个到期成员没有被本轮认领（例如批次已满，或认领尚未过期），
  reconcile 会把它的 score 推到推导出的时间。因此每个滞留成员每个 tick 最多引起一次
  数据库唤醒，而且会随着它进入终态而消失，不会形成热循环。

## 清理与对齐

- `TRASH_PURGE_INTERVAL_SECONDS` 的默认值改为 86400，涉及 `app/config.py`、
  `app/ingest/tasks.py` 的回退值和 `.env.example`。
- 新增 `INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS`（默认 86400，必须为正整数，
  否则 fail closed），在 `config.py` 中校验，与现有间隔变量的写法一致。
- 每日清理和每日全量修复使用相同的默认间隔。Celery Beat 的 interval 调度以各自的
  `last_run_at` 为基准，首次部署时两者可能相差最多 1 小时。所以空闲时每天最多唤醒
  2 次，约 10 分钟，约 0.04 CU-h/天。为了不引入 crontab 和时区配置，接受这一点。

## 预期效果

| 状态 | 空闲唤醒 | CU-h/天 |
| --- | --- | --- |
| 现在 | 每 10 分钟 1 次 + 每小时 1 次 | 约 3 |
| 本任务之后 | 每天至多 2 次 | 约 0.04 |

## 兼容性与回滚

- 回滚到旧代码：旧 Beat 条目照常运行（600 秒，不经门控），新任务名没有调度方，
  Redis 里遗留的两个键没有危害。
- 如果新代码出现门控 bug 导致漏扫，可以临时把
  `INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS` 调小来强制频繁全量扫描，
  也可以删除 baseline 键来触发一次全量扫描。
- 不需要数据库迁移。

## 本地 `.env`

把 `.env` 第 51 行改为 `INGEST_NOTIFICATION_INTERVAL_SECONDS=600`。门控上线之后，
即使本地用 `full` 模式启动 Beat，空闲 tick 也只访问本地 Redis，所以只改这一行就足够。
`DATABASE_URL` 指向生产库这个更大的隐患不在本任务范围内。
