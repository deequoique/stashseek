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
repair task: app.ingest.tasks.deliver_pending_ingest_notifications_task
repair schedule: INGEST_NOTIFICATION_INTERVAL_SECONDS (default 600 seconds)
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
  `0` for an empty eligible backlog.
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
