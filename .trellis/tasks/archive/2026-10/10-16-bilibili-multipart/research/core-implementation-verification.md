# Core implementation handoff — 2026-10-16

Later validation: [real Redis Lua execution](./redis-live-gate-success.md)
passed six tests on a separately owned temporary Redis 6.2.11 instance. The
original outstanding note below records the earlier implementation handoff.

Implemented the nullable typed source schema and additive revision
f8a9b0c1d2e3 after e2f3a4b5c6d7, fixed-host bounded public-metadata child,
tenant-bound HKDF/HMAC refs, shared Redis gate, strict canonical DTOs/routes,
shared prepared admission, legacy consent and known-CID dedupe, owned CID→P
source navigation, runtime wiring and inert exporter composition.

The public client uses a repository-owned killable subprocess with a 15-second
whole-call timeout, 1 MiB child output cap, verified configured CA bundle,
ProxyHandler({}), no redirect/cookies/authentication and fixed endpoint/validated
parameters. The worker low-level client deliberately has no browser tenant
admission; browser preview/source admission and actual upstream leases belong to
the BilibiliService. No actual video import was performed.

## Evidence

- New core/API tests: **43 passed**:
  tests/test_bilibili_parts_core.py and tests/test_bilibili_parts_api.py.
- Explicit isolated local PostgreSQL 17.9 source tests: **14 passed**:
  tests/test_bilibili_parts_postgres.py with the root-provided
  STASHSEEK_TEST_DATABASE_URL at loopback port 55439. These create/drop only
  random per-test schemas, including a miniature legacy table for the source
  migration roundtrip. They do not run the full historical migration chain.
- Combined focused legacy/core regression: **256 passed** with the inert
  POSTGRES_PASSWORD=isolated-unit-test environment: core, Bilibili URL,
  submission, Web public/library/runtime tests.
- Alembic reports one head, f8a9b0c1d2e3. git diff --check passed.

Real Redis Lua execution is still an independent-check concern. Unit tests
verify bounded rate/lease/release and fail-closed service behavior with injected
Redis. No Redis server was available to this agent. The root recorded the
separate read-only real public metadata canary.

The extractor/worker and complete browser projection are owned by sibling
implement agents and require integration review after their work lands.
The generic legacy CLI has no actual-CID evidence, so this core does not invent
or backfill an old record's pin. Unknown legacy overlap remains per-entry
default-false consent, while known identical CID reuses its owned item.

No commits, production/shared migrations, deployment or account/video writes
were performed.
