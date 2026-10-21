# Implementation Plan

## 约束（所有子代理都必须遵守）

- 共享的 dev 工作区里，`app/ingest/tasks.py`、`app/config.py`、`app/diagnostics.py`、
  `.env.example`、`docs/reference/configuration.md`、`database-guidelines.md`
  都有其他会话留下的未提交改动（WeMM 等）。**实现必须在独立 worktree 中进行**，
  从 dev 的 HEAD 切出，分支名 `fix/neon-idle-scale-to-zero`。不得在共享工作区
  修改这些文件。
- 实现由 `trellis-implement` 子代理完成（`model: sonnet`）。主会话负责审查、验证、合并和提交。
- **pytest 必须覆盖 `DATABASE_URL`**：使用主工作区 `.env.neon-test` 中的测试分支 DSN，
  scheme 改写为 `postgresql+psycopg://`，并断言 host 以 `ep-falling-fog-` 开头。
  禁止直接运行 bare pytest（会连到生产库）。
- 不得 SSH 到生产主机，不得修改 Neon 项目，不得 push 到 main。

## 有序清单

1. [ ] 在 dev 上按路径提交规划文档（`git commit -- .trellis/tasks/10-16-neon-idle-scale-to-zero`），
   然后从 dev HEAD 创建 worktree。
2. [ ] 新增 `app/ingest/notification_repair_index.py`：Protocol、Redis 实现（ZADD LT、
   Lua compare-and-set、≤0.5 秒超时）、测试用内存实现，以及推导函数 `repair_due_at`。
3. [ ] `app/ingest/notifications.py`：
   - `IngestNotificationPoller` 增加可选参数 `repair_index`；
   - 新增 `repair_tick()`（门控模式）和 `full_repair()`（全量加重建加 baseline）；
   - `deliver_event()` 结束后执行 reconcile；`redrive_failed()` 之后执行 `mark_due(now)`；
   - 空闲心跳带 `idle_skipped=1`；诊断白名单增加新字段和新事件名。
4. [ ] `app/ingest/tasks.py`：
   - post-commit 段写入 `mark_due(now + claim_timeout)`，入队失败时写入 `mark_due(now)`；
   - `deliver_pending_ingest_notifications_task` 改为调用 `repair_tick()`；
   - 新增 `repair_all_ingest_notifications_task` 及其路由和 Beat 条目；
   - 清理任务的回退默认值改为 86400；
   - 新间隔变量 fail closed。
5. [ ] `app/config.py`：`trash_purge_interval_seconds` 默认改为 86400；
   新增 `ingest_notification_full_sweep_interval_seconds` 及其校验。
6. [ ] 测试（`tests/test_ingest_notifications.py`、`tests/test_ingest_completion.py`、
   `tests/test_tasks.py`，必要时新增 `tests/test_notification_repair_index.py`）：
   - 空闲 tick 不调用 session factory，并发出 `idle_skipped` 心跳；
   - 完成事件提交后，定向任务丢失时，认领超时后的 tick 会去扫描；
   - 可重试失败在到期前不访问数据库，到期后访问；
   - 进入终态后移除成员，之后的 tick 不再访问数据库；
   - Redis 读异常时放行，写异常不影响结果；
   - baseline 缺失时执行全量扫描并写入 baseline；全量扫描能重建索引；
   - reconcile 的比较设置能抵御并发的 `mark_due`；
   - Beat 调度包含两个通知条目，清理默认 86400，非法的新间隔值 fail closed；
   - Redis 实现的测试：本地 Redis 可用时，使用独立的 db 编号和随机前缀；不可用时 skip。
7. [ ] 文档与规范：`.env.example`、`docs/reference/configuration.md`、
   `docs/how-to/troubleshoot.md`（如涉及）、`.trellis/spec/backend/database-guidelines.md`、
   `.trellis/spec/backend/ingest-completion-queue.md`。
8. [ ] 主工作区本地 `.env`：第 51 行改为 `INGEST_NOTIFICATION_INTERVAL_SECONDS=600`（不提交）。

## 验证命令（在 worktree 中执行）

```bash
export DATABASE_URL="<.env.neon-test 的 DSN，改写为 postgresql+psycopg://，host 必须是 ep-falling-fog-…>"
.venv/bin/python -m pytest tests/test_ingest_notifications.py tests/test_ingest_completion.py tests/test_tasks.py tests/test_item_management_tools.py -q
.venv/bin/python -m compileall -q app
```

worktree 里没有 `.venv` 时，使用主工作区的 `/Volumes/PeeB/projects/notebook-agent/.venv/bin/python`，
并把 `PYTHONPATH` 指向 worktree 根目录。

## 审查关口

- 第 2–5 步完成后，由 `trellis-check` 审查：spec 合规、诊断隐私、Redis 超时、失败放行路径。
- 主会话审查 diff，确认没有混入共享工作区其他会话的改动。
- 合并回 dev（worktree 分支 → dev），提交时按路径提交。

## 上线（需要用户逐步授权，不在本任务的自动范围内）

1. 开 dev → main 的 PR；合并后自动部署，等待所有者在 GitHub 的 Production 门审批。
2. 检查生产 `/etc/notebook-agent/notebook-agent.env` 中是否显式设置了
   `TRASH_PURGE_INTERVAL_SECONDS=3600` 或较短的 `INGEST_NOTIFICATION_INTERVAL_SECONDS`。
   如果有，改为新默认值或删除该行（需要用户授权 SSH）。
3. 上线后验证：
   - Neon Monitoring 的 Last day 视图：空闲时段持续为 endpoint inactive；
   - beat 和 worker 日志：空闲 tick 出现 `idle_skipped=1`，每天出现一次全量扫描；
   - 提交一次测试摄入：定向通知即时送达，之后不再有周期性唤醒。

## 回滚点

- 代码：revert 合并提交即可，无数据库迁移。
- 运行时：删除 baseline 键可强制一次全量扫描；临时调小
  `INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS` 可恢复频繁兜底。
