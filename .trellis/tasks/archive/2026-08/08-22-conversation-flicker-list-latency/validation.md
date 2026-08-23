# Validation evidence

## Correctness and query shape

- Focused Session cache, email auth, conversation-list, and conversation-stream
  tests passed before review: 44 tests.
- After the independent cache-race fixes, the focused Session cache, email
  auth, conversation-list, and SSE set passed: 54 tests.
- Identity-merge-focused plus Session-cache tests passed where supported:
  11 passed and 2 PostgreSQL-only integration tests skipped in the local
  SQLite environment.
- Conversation-list query-count tests prove one projection SQL statement for
  0, 1, and 30 rows, and two statements for a cursor page (one ownership read
  plus one projection query).

## Frontend gates

- After independent review fixes, the bundled Node 22 runtime passed all 140
  frontend tests, full ESLint, TypeScript typecheck, Vite production build, and
  OpenAPI stale/type checks.

## Measured baseline and rollout evidence

- The pre-change zero-conversation remote development baseline was 2.978 s and
  3.131 s for `GET /api/v1/conversations?limit=30`.
- A fresh Mailpit fake-email login against `https://localhost:8443` measured a
  2.300 s cold list request followed by warm samples of 1.038, 0.999, 1.109,
  and 1.017 s (median about 1.03 s). An earlier warm run measured 1.010-1.135 s.
  This is a material reduction from the roughly 3 s baseline, but remains just
  above the sub-second target under the remote Neon topology.
- Redis inspection showed one generation key and one Session projection key.
  Fixed operation-count evidence is: one Redis lookup and no auth database
  SELECT on a warm Session hit; one joined auth SELECT on a cold resolution;
  and one conversation-list projection SQL independent of page size.
- Read-only PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` reported 0.319 ms planning
  and 0.073 ms execution for the paged projection on the empty fake-email
  tenant. The remaining ~1 s is remote connection/network overhead, so no
  speculative index migration was added.
- `WEB_SESSION_CACHE_TTL_SECONDS=0` remains the database-only rollback switch.

## Known unrelated failures / environment constraints

- The sandboxed full Python run reached 755 passed / 73 skipped but had the
  existing unrelated Agent retrieval assertion, loopback-bind errors, and two
  production-settings failures caused by the shell's wildcard browser companion
  origin.
- A loopback-enabled run excluding that known Agent assertion reached 531
  passed / 10 skipped before the remote PostgreSQL server closed the shared
  connection. Four failures were the migration test and subsequent tests using
  that same broken connection; the run was stopped after 16 minutes.
- Final Alembic validation passed: one head at `b8c9d0e1f2a3`, and `alembic
  check` reported no new upgrade operations.
