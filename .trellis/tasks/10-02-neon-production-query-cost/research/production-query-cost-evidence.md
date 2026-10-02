# Production Query Cost Evidence

## Repository evidence

- `app/ingest/tasks.py` builds the Beat schedule at import time. The notification task uses `INGEST_NOTIFICATION_INTERVAL_SECONDS`, defaulting to `10` seconds in both the task fallback and `app/config.py`.
- `IngestNotificationPoller.sweep_once()` calls `_claim_batch()` for every tick. `_claim_batch()` reads PostgreSQL time and executes an outer-join candidate query even when it returns no rows. A successful tick then calls `_oldest_eligible_backlog_age_seconds()`, which reads time and runs a second aggregate query for the heartbeat.
- `deploy/systemd/notebook-agent-beat.service` starts one long-lived Beat process in production. Its environment file does not set a notification interval, so the application default applies unless an operator has added a private override.
- `app/deployment.py` selects `full` when no profile is specified. The `full` plan includes `worker` and `beat`; `read` includes only MCP.
- Completion events are already durable and the notification ledger has claim tokens, stale-claim recovery, bounded retries, and terminal dispositions. This is a suitable idempotency boundary for an event-triggered task plus a slow repair sweep.

## Estimated idle query pressure

At the default 10-second schedule there are 8,640 sweeps per day. An empty successful sweep executes multiple SQL statements, including time reads, the candidate query, and the heartbeat observation. The exact total depends on driver/session behavior and whether events are pending; the order of magnitude is tens of thousands of statements per day before application traffic.

## Production diagnostics attempted

Using the redacted local `.env` connection strings, a read-only `psql` connection was attempted against both the pooled and direct Neon endpoints. DNS resolved through the execution environment, but both endpoints closed the connection before authentication/query execution. Therefore this task does not claim a production `pg_stat_statements` ranking. No mutation was attempted.

## Proposed verification after rollout

1. Run read-only `pg_stat_statements` queries for call count and total rows, filtering for the notification candidate query and its targeted event query.
2. Compare Neon daily `compute_unit_seconds` before/after deployment and inspect whether idle periods can suspend when no HTTP/user traffic exists.
3. Confirm notification latency for a successful ingestion and confirm a broker/enqueue failure is repaired by the 10-minute sweep.
