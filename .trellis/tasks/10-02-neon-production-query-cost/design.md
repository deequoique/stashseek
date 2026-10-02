# Technical Design

## Boundaries

The completion event transaction remains the source of truth. Once `_complete_dispatch` or `_mark_dispatch_failed` commits an event, the caller makes a best-effort Celery enqueue for that event ID. The enqueue is outside the database transaction and cannot change the ingestion result.

`IngestNotificationPoller` gains an event-scoped delivery path that reuses the current delivery ledger claim/ACK rules. It must never run the global outer-join candidate scan for an event-triggered task. Duplicate Celery messages converge through the existing `(event_id, handler_key)` row and claim token.

The existing global sweep remains as a repair mechanism. Its schedule changes from 10 seconds to 600 seconds by default; explicit operator values remain supported. The sweep still handles historical events, enqueue failures, stale claims, and manually redriven rows.

## Data flow

```text
terminal ingest transaction
  -> commit IngestCompletionEvent
  -> best-effort Celery task(event_id)
  -> event-scoped claim in IngestCompletionDelivery
  -> load target / send outbound / token-fenced ACK

broker failure or crash
  -> durable event remains eligible
  -> 600-second bounded repair sweep claims it
```

## Compatibility and rollback

- Existing notification message text, delivery states, retry limits, and database schema remain unchanged.
- If the targeted enqueue path is disabled or fails, the repair sweep preserves delivery, with a bounded worst-case delay equal to the configured repair interval.
- Operators can temporarily restore a shorter interval through `INGEST_NOTIFICATION_INTERVAL_SECONDS` while diagnosing delivery, but the production default must stay at 600 seconds.
- The launcher `read` default is a local safety change only; explicit `--profile full` and `--profile langbot` retain current component sets.

## Operational considerations

- Enqueue failures must be logged only through the existing privacy-safe diagnostic event; no broker exception text or credentials may be emitted.
- The deployment documentation must state that production notification latency is event-driven in the normal case and that the repair interval is not the normal delivery path.
- The rollout must be deployed through the existing immutable release/migration gates; no direct database mutation is part of this task.
