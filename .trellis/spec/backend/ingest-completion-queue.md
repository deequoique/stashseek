# Ingestion Completion Outbox and Queue

## Scope

Use this contract whenever a worker terminal state must trigger asynchronous
notifications, workflow orchestration, or another internal process. It covers
the `IngestDispatch` terminal transaction, the completion outbox, broker
publication, repair sweeps, Redis durability, deletion races, and the future
consumer boundary.

## Source-of-truth and event contract

- PostgreSQL is the business source of truth. A broker result or Celery result
  backend entry never determines whether content is ready.
- One dispatch attempt has at most one `IngestCompletionEvent`, enforced by a
  unique `dispatch_id`. Create the event in the same locked transaction that
  changes the dispatch to `completed` or `failed`.
- A normal worker return snapshots `ready`, `needs_extension`, or `needs_asr`
  with `outcome=completed`. A terminal failure snapshots `failed` with a
  non-null stable error code. Retry release and duplicate delivery create no
  event.
- `ContentItem.ready` wins the crash window after `process_item()` commits but
  before dispatch finalization. A late failure hook must converge the dispatch
  to `completed` and create/reuse `completed/ready`; it must not downgrade a
  searchable item or emit a contradictory failure.
- Emit `completion_event_created` only after the transaction commits. A rolled
  back insert must not leave a diagnostic claiming that the event exists.

## Broker boundary

The historical completion broker publisher is now a rollback-compatible,
disabled path. The authoritative user-facing sink is an event-scoped
notification task, with the PostgreSQL poller retained only as repair:

```text
handler: source-channel.notification.v1
task: app.ingest.tasks.deliver_ingest_notification_task
queue: maintenance
payload: [completion_event_id]
repair tick: app.ingest.tasks.deliver_pending_ingest_notifications_task
repair tick schedule: INGEST_NOTIFICATION_INTERVAL_SECONDS (default 600 seconds), Redis-gated
full repair: app.ingest.tasks.repair_all_ingest_notifications_task
full repair schedule: INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS (default 86400 seconds), ungated
```

Terminal worker hooks create the durable event and best-effort enqueue only its
internal event ID after commit. The targeted task claims a separate
``ingest_completion_delivery`` row with ``UNIQUE(event_id, handler_key)`` and
never runs the global candidate scan. If enqueue fails, the durable event is
claimed by the bounded repair sweep. Only the existing ``ingest,maintenance``
worker is required. The retired
``ingest-completion`` queue must not be added to that worker; operators stop
old producers, verify database event coverage, and explicitly inspect/drain
old backlog before deleting it. A future broker subscriber must use a distinct
handler ownership contract and must never send the same source-channel
notification alongside this poller.

```text
queue: ingest-completion
task name: app.ingest.completion.consume
payload: [completion_event_id]
delivery mode: persistent
```

- The payload contains only the internal completion event ID. It must not
  contain tenant/channel identities, URLs, titles, transcripts, segments,
  embeddings, storage keys, model content, credentials, or exception text.
- The producer may publish the stable task name without registering a local
  consumer. Until a real idempotent consumer exists, no worker may listen to
  `ingest-completion`; an unregistered-task worker would acknowledge and
  discard the message.
- Delivery is at least once, not exactly once. A crash after broker acceptance
  and before the outbox acknowledgement may publish the same event ID again.
  Every consumer must use the stable event ID as an idempotency key.
- Mark an outbox row `enqueued` only after the broker accepts the persistent
  message. A publish failure leaves or restores the row to a retryable state
  and never changes the already-committed ingestion result.
- Broker durability is part of this guarantee. The bundled Redis uses a
  persistent volume, AOF, and `appendfsync=always`. A remote broker must provide
  equivalent durability before acknowledging a publish; periodic snapshots
  alone are insufficient because accepted `enqueued` rows are not swept again.

## Redis repair index and gated tick

PostgreSQL stays the only source of truth. The repair tick is gated by a
**disposable** Redis structure so an idle production database receives no
periodic traffic (Neon scale-to-zero; see `database-guidelines.md`).

```text
due:      notebook-agent:ingest-notification-repair:v1:due       ZSET
          member = str(event_id), score = integer epoch seconds
baseline: notebook-agent:ingest-notification-repair:v1:baseline  STRING, no TTL
          written only after a full repair rebuilt the index from PostgreSQL
```

- `repair_due_at()` (`app/ingest/notification_repair_index.py`) is the single
  derivation of a member's due time from the ledger: no delivery row -> now;
  `claimed` -> `claimed_at + claim timeout`; retryable `failed` ->
  `next_attempt_at`; `succeeded`, `terminal_failure`, `retry_exhausted`, or a
  deleted event -> `None` (remove from the index). Round times up so a member
  is never due before PostgreSQL would consider the row eligible.
- Write points, all best effort (a Redis exception emits only the numeric
  `notification_repair_index_write_failed` diagnostic and never changes the
  ingestion or delivery result): `mark_due(event_id, now + claim timeout)`
  after the terminal event commits and before the targeted enqueue;
  `mark_due(event_id, now)` when the enqueue fails or the event is created by
  the deleted-item claim path (no targeted task); a post-delivery reconcile in
  `deliver_event`; `mark_due(event_id, now)` after a successful
  `redrive_failed`. `mark_due` is `ZADD LT`: the earliest time wins.
- Reconcile is compare-and-set (Lua): it only changes or removes a member whose
  score still equals the one the caller observed, so a stale derivation can
  never erase a concurrent `mark_due` such as an operator redrive. Observe the
  score before reading the ledger. The reconcile SQL in the targeted
  `deliver_event` path takes its `statement_timeout` from the remaining
  delivery deadline and is skipped (members stay due) when that is exhausted.
- Redis client timeouts (connect and read) never exceed 0.5 seconds.
- The tick has three modes:
  - **Idle** (baseline present, nothing due): zero PostgreSQL statements. Emit
    `notification_poller_heartbeat` with `heartbeat=1`, zero counters,
    `observability_failed=0`, `idle_skipped=1`, and no
    `oldest_eligible_backlog_age_seconds` (no query ran, so the "empty backlog
    is age 0" convention does not apply).
  - **Due**: run the normal bounded `sweep_once`, then reconcile the swept and
    due members against the ledger. Members the sweep could not finish stay due
    (at most one database visit per tick; no hot loop because an ineligible row
    is rescheduled to its derived time).
  - **Fail open**: Redis unreadable -> the original ungated `sweep_once` plus
    `notification_repair_gate` `repair_gate_unavailable=1`. Baseline missing
    (fresh or lost Redis) -> one full repair plus `baseline_missing=1`.
- The full repair (`repair_all_ingest_notifications_task`, once a day by
  default, never gated) runs `sweep_once`, rebuilds the index from every
  non-terminal ledger row and delivery-less event (bounded to 1000), removes
  members that are terminal or gone, and then writes the baseline. When the
  non-terminal set exceeds the bound it emits `truncated=1`, withholds the
  baseline and clears any earlier one, so the next tick repeats the full repair
  (the sweep claims events regardless of the index) until the backlog drains.
  It bounds the notification delay after any lost Redis write to one interval.
- Rollback: the old code ignores both keys and keeps scheduling the original
  ungated tick. Deleting the baseline key forces one full repair.

## Repair sweep

- The legacy broker repair sweep is retained only for rollback compatibility
  and is not scheduled by the current Beat configuration. The notification
  poller performs its own bounded database claim/ACK sweep on `maintenance`.
- Claim bounded batches with PostgreSQL time, `FOR UPDATE SKIP LOCKED`, a
  random claim token, and a claim timeout. Never hold a database transaction
  across broker I/O.
- When candidate discovery outer-joins events to an optional delivery row,
  qualify the lock as `FOR UPDATE OF ingest_completion_event SKIP LOCKED`.
  PostgreSQL rejects an unqualified `FOR UPDATE` on the nullable side of an
  outer join. The event row is the serialization root for inserting or
  reclaiming the unique handler delivery.
- Apply a parameterized PostgreSQL `statement_timeout` derived from the
  remaining whole-sweep budget to claim, acknowledgement, and release SQL.
- Reserve time for token-fenced ACK/release before the sweep deadline. If a
  batch is claimed but an event has not started outbound I/O, release it as an
  immediately eligible deferred delivery without consuming a transport retry
  attempt; do not hide it behind the much longer stale-claim timeout.
- Source-thread validation is part of admission correctness. Unsupported,
  stale, disabled, or tenant-mismatched trusted records resolve to no target,
  but a database/programming failure must abort admission rather than commit a
  targetless dispatch that can never notify its source conversation.
- Notification observability is best effort and numeric only. Cap an oldest-
  eligible-age query to its explicit observation reserve (at most 10% of the
  sweep and 250 ms), isolate its failure from delivery state, and reserve age
  `0` for an empty eligible backlog. A gated idle tick runs no query and omits
  the age field instead.
- Isolate each event. A publish failure for one event must not block peers.
  When the deadline expires after broker acceptance, leave the claim for stale
  recovery rather than starting unbounded SQL; the resulting duplicate is
  valid at-least-once behavior.
- Diagnostics expose only fixed stages, internal event IDs, stable error codes,
  counters, and duration. Never serialize driver/broker exception messages.

## Deletion and rollback

- Completion events reference both dispatch and item with cascading deletion.
  A message that arrives after physical purge must resolve to a safe no-op.
- A soft-deleted/purge-claimed item converges to the existing `item_deleted`
  terminal failure contract. Restore/re-save may create a new dispatch and a
  new completion event; it does not reuse the old attempt.
- Code rollback preserves the forward-compatible outbox schema and pending or
  claimed rows. Stop only completion publication/consumption when necessary;
  do not stop ingestion truth writes or destructively drop audit state.

## Required validation

- Cover all successful terminal states, terminal failure, retry-without-event,
  duplicate delivery, repeated failure hooks, and the ready-before-finalize
  crash window.
- Use PostgreSQL integration tests to prove dispatch/event atomic rollback,
  unique event creation, stale-claim recovery, peer isolation, duplicate
  publication after acknowledgement crash, and physical-purge cascade.
- Verify the declared queue is durable, the current app has no registered
  completion consumer, and the serialized payload contains only the internal
  event ID.
- Verify local Redis AOF/fsync configuration, single Alembic head, model and
  migration constraint parity, expected hosted revision, upgrade/downgrade
  roundtrip, privacy-safe logs, and bounded statement-timeout SQL.
- Repair-index coverage: an idle tick never opens a PostgreSQL session; a lost
  targeted task is found by the first tick after the claim timeout; a retryable
  failure is not visited before `next_attempt_at`; terminal events leave the
  index and later ticks stay idle; Redis read failure fails open and write
  failure changes no result; a missing baseline triggers one full sweep; the
  full repair rebuilds the index and withholds the baseline when truncated;
  reconcile loses to a concurrent `mark_due`. Redis-implementation tests use a
  real local Redis on logical db 15 with a random key prefix and skip when it
  is unreachable.

## Wrong vs correct

Wrong:

```python
db.commit()
send_notification({"user_id": item.user_id, "url": item.url})
```

This has a commit/publish loss window, leaks private data to the broker, and
couples ingestion success to notification availability.

Correct:

```python
# Inside the dispatch row-lock transaction:
event = ensure_completion_event(dispatch_id, item_state="ready")
db.commit()

# After commit; bounded and independently recoverable:
publish_completion_event(event.id)
```

The database records the durable intent atomically, while the broker carries
only an idempotency key and may safely deliver it more than once.
