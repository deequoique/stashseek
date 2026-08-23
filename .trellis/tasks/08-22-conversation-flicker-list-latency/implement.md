# Implementation Plan

## 1. Establish regression evidence

- [x] Add a frontend test that holds transcript refetch pending after a terminal SSE answer and proves the answer never disappears.
- [x] Add a frontend test that proves convergence renders exactly one persisted answer.
- [x] Add a new-conversation test that holds conversation-list refetch pending and proves the composer is enabled after reset.
- [x] Add backend SQL-count tests for 0, 1 and 30 conversation threads, including completed-only latest turn, empty threads, tenant isolation and cursor pagination.
- [x] Add auth query-count tests showing the current/target request-scoped resolution contract.

## 2. Consolidate database Session resolution

- [x] Introduce one internal resolved-Session projection shared by email auth, CSRF and conversation dependencies.
- [x] Replace separate Session/user/identity lookups with one joined fail-closed query.
- [x] Throttle `last_used_at` to cold/cache-expired resolution instead of every request.
- [x] Preserve cookie names, hash-only storage, expiry, revocation, disabled-user/identity and tenant namespace checks.

## 3. Add bounded Redis read-through caching

- [x] Implement a strict versioned cache adapter using the existing Redis client and token digest keys.
- [x] Add global generation startup/invalidation and generation-safe cold fills.
- [x] Bound TTL by configuration and Session expiry; make `0` a database-only rollback mode.
- [x] Treat Redis timeout, outage and corrupt values as misses with PostgreSQL fallback.
- [x] Ensure no raw token, CSRF value, email, key/value or internal identity enters logs.

## 4. Reuse auth within one request

- [x] Extend the existing secure boundary to store the validated projection on `request.state`.
- [x] Make route dependencies reuse that projection without another Session resolution.
- [x] Validate unsafe methods with exact Origin, double-submit equality and the cached projection's CSRF hash.
- [x] Cover auth routes that bypass the unsafe boundary without creating a second resolver.

## 5. Wire credential invalidation

- [x] Increment cache generation after database logout/revoke.
- [x] Integrate user disable and absorbed-account/session revocation paths.
- [x] Integrate Web/channel identity merge completion.
- [x] On Redis failure, require a generation bump before cache hits resume.
- [x] Test concurrent cold fill versus invalidation ordering.

## 6. Replace conversation N+1

- [x] Build one set-based latest-completed-turn projection over the paged tenant thread set.
- [x] Preserve empty thread title, preview truncation, ordering and opaque cursor behavior.
- [x] Inspect generated PostgreSQL SQL and `EXPLAIN`; add an Alembic index only if measured evidence requires it.
- [x] Prove SQL count remains constant at 30 items.

## 7. Fix frontend transitions

- [x] Keep terminal answer/citations/timeline visible until transcript cache convergence.
- [x] Run history invalidation in the background so it cannot hold the send mutation pending.
- [x] Keep terminal projection visible on transcript sync failure with a retry path.
- [x] End new-conversation pending state after reset and refresh/optimistically update the sidebar independently.
- [x] Preserve private query-cache teardown and prevent duplicate answers.

## 8. Documentation and contract review

- [x] Update Web/browser runtime and frontend state/query specs with request-scoped auth reuse, Redis cache authority and answer handoff rules.
- [x] Update environment/deployment docs for Session cache TTL and database-only rollback.
- [x] Run OpenAPI export/check; regenerate only if an actual public schema change is introduced.
- [x] Record before/after request timings and SQL/Redis operation counts in `validation.md`.

## 9. Validation gates

```text
.venv/bin/pytest -q <focused web auth, conversation route and streaming tests>
.venv/bin/pytest -q
pnpm --dir web test
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
pnpm --dir web check:api
.venv/bin/alembic heads
.venv/bin/alembic check
git diff --check
python3 ./.trellis/scripts/task.py validate 08-22-conversation-flicker-list-latency
```

## Review and rollback gates

- [x] Independent full-scope review covers cache invalidation races, tenant leakage, CSRF reuse, Redis outage recovery, SQL pagination and React intermediate frames.
- [x] If Redis validation fails, set cache TTL to `0` and retain the database/query/frontend improvements.
- [x] Do not deploy an index migration unless the migration admission and downgrade/rollback shape are reviewed.
