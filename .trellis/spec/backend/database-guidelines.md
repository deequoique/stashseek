# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

StashSeek Chat uses SQLAlchemy and Alembic with PostgreSQL. Production runtime
traffic uses a pooled Neon URL, while schema migrations use the matching direct
Neon URL in a bounded one-shot unit outside application build and request
lifecycles.

## Scenario: Keep the production Neon schema synchronized with Alembic

### 1. Scope / Trigger

Apply this contract whenever adding or merging an Alembic migration, migrating
the production Neon database, or deploying a production release. It prevents a
release from starting against an incompatible or partially migrated schema.

### 2. Signatures

```dotenv
# Long-running application processes; must be a pooled Neon URL.
DATABASE_URL=postgresql://ROLE:PASSWORD@HOST-pooler.REGION.neon.tech/DB?sslmode=require

# One-shot migration unit only; must use the direct hostname.
MIGRATION_DATABASE_URL=postgresql+psycopg://ROLE:PASSWORD@HOST.REGION.neon.tech/DB?sslmode=require
```

```bash
alembic heads
alembic upgrade head
alembic current
```

### 3. Contracts

- The repository must have exactly one Alembic head.
- Long-running Web/MCP, worker, and Beat units receive only `DATABASE_URL`.
  They must never inherit `MIGRATION_DATABASE_URL`.
- The one-shot migration unit maps `MIGRATION_DATABASE_URL` to `DATABASE_URL`,
  then runs `alembic upgrade head`, `alembic current`, and `alembic check`
  before any candidate application unit starts.
- Never run migrations from an application build, import, or request handler.
- Both URLs remain in separate root-owned mode-`0600` server environment files
  and never enter GitHub Actions or repository files.
- The shared development database has one designated migration operator at a
  time. Destructive tests use an isolated Neon branch or a local PostgreSQL
  database, not the shared `main` branch.
- Responses and logs may expose only the verified revision, never a DSN,
  database password, provider exception message, or stack trace.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Repository has multiple Alembic heads | Validation fails; do not deploy or migrate. |
| Runtime URL is direct or migration URL is pooled | Stop admission and correct the isolated server files. |
| Migration/current/check fails | Do not start the candidate; restore the previous release pointer. |
| Long-running unit contains the migration URL | Static deployment test fails before commit. |
| Migration or connection fails | Preserve production data and report only a safe failure category. |

### 5. Good / Base / Bad Cases

- Good: a reviewed migration reaches `main`; the approved release uses the
  direct URL in its one-shot admission, then starts long-running processes with
  only the pooled URL.
- Base: a code-only deployment runs idempotent migration admission and confirms
  the existing single head before startup.
- Bad: migrate through the pooler, expose the direct URL to long-running units,
  print either DSN, or let every collaborator migrate production concurrently.

### 6. Tests Required

- Assert `ScriptDirectory.from_config(...).get_current_head()` returns one head.
- Static deployment tests must prove migration/runtime environment-file
  separation and the `upgrade`/`current`/`check` sequence.
- Before application startup, query `alembic_version.version_num` without
  printing the connection URL and require it to equal the repository head.
- Exercise migration failure rollback without deleting or downgrading data.

### 7. Wrong vs Correct

#### Wrong

```ini
EnvironmentFile=/etc/notebook-agent/notebook-agent.env
# The same file contains DATABASE_URL and MIGRATION_DATABASE_URL.
```

Every long-running process can now read the direct migration credential.

#### Correct

```ini
# Long-running unit
EnvironmentFile=/etc/notebook-agent/notebook-agent.env

# One-shot migration unit only
EnvironmentFile=/etc/notebook-agent/migrations.env
ExecStart=/opt/notebook-agent/current/deploy/scripts/run-production-migrations
```

The direct credential exists only for bounded migration admission.

---

## Scenario: Never hold a development Neon compute awake

### 1. Scope / Trigger

Apply this contract whenever running the application against a Neon
`DATABASE_URL` outside production: local development, manual testing, eval
runs, and any unattended process left running between work sessions. Neon
bills compute by the second while the endpoint is active, so an idle-but-awake
development compute costs the same as a serving one.

### 2. Signatures

```bash
# profile `read` -> components ("mcp",). No worker, no Beat, no DB polling.
./scripts/stashseek start --profile read

# profile `full` -> ("worker", "beat", "mcp", "gateway"). Beat polls the DB.
./scripts/stashseek start --profile full
./scripts/stashseek stop
```

```dotenv
# Beat's bounded notification repair sweep queries PostgreSQL once per tick.
# Normal delivery is event-triggered through Celery.
INGEST_NOTIFICATION_INTERVAL_SECONDS=600
TRASH_PURGE_INTERVAL_SECONDS=3600
```

### 3. Contracts

- Neon suspends a compute only after its suspend timeout of inactivity
  (project default: 300s). Any recurring database access with a period shorter
  than that timeout makes scale-to-zero unreachable for as long as the process
  lives.
- Terminal completion events enqueue
  `app.ingest.tasks.deliver_ingest_notification_task` with the internal event
  ID after commit. Its targeted claim path never runs the global candidate
  scan. `app.ingest.tasks.deliver_pending_ingest_notifications_task` remains a
  bounded repair sweep every `INGEST_NOTIFICATION_INTERVAL_SECONDS` (default
  600) for enqueue/broker failures and historical backlog.
- Development work that does not exercise ingestion uses the `read` profile.
  Only start `full` / `langbot` for the duration of an ingestion test, then
  stop it.
- Never leave a `full` / `langbot` runtime, a notebook kernel, a REPL, or a
  paused debugger holding `app.db.get_engine()` open overnight. The engine is
  `@lru_cache`d with SQLAlchemy's default `QueuePool` (`pool_size=5`,
  `pool_recycle=-1`), so pooled connections are never retired by age.
- For long unattended sessions, either shut the runtime down or point
  `DATABASE_URL` at a local PostgreSQL instead of Neon.
- Never set a development endpoint's `suspend_timeout_seconds` to `-1`
  (never suspend). `0` means "use the project default" and is correct.
- Cost of getting this wrong, at the observed 0.5 CU: `0.5 x 86400 = 43,200`
  CU-seconds = **12 CU-hours per day**, every day, for zero served traffic.
  Even at the 0.25 CU autoscaling floor it is 6 CU-hours per day.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Daily `compute_unit_seconds` is flat at `min_cu x 86400` | Treat as a stuck-awake compute, not as real load; find the poller and stop it. |
| Ingestion test finished | Stop the `full` / `langbot` runtime; do not leave Beat running. |
| A new periodic task needs a period shorter than the suspend timeout | It must be justified for production and must not be scheduled by default in development. |
| Endpoint reports `suspend_timeout_seconds: -1` on a development branch | Restore it to `0` and record why it was changed. |
| Unattended work needs a live database for hours | Use local PostgreSQL, not a Neon branch. |

### 5. Good / Base / Bad Cases

- Good: a developer runs `--profile read` all day; the Neon compute suspends
  within 5 minutes of each burst of query activity and daily CU is a few
  CU-minutes.
- Base: an ingestion test runs `--profile full` for 40 minutes and is then
  stopped; that day shows a single bounded block of CU, not a flat line.
- Bad: `--profile full` is left running over a weekend; Beat's 10-second sweep
  keeps the compute awake continuously and burns 12 CU-hours per day with no
  user traffic.

### 6. Verification Required

- Audit development CU with the Neon consumption API before assuming a bill is
  real load:

  ```bash
  neon api "/consumption_history/v2/projects\
  ?from=<ISO8601>&to=<ISO8601>&granularity=daily\
  &org_id=<ORG_ID>&metrics=compute_unit_seconds"
  ```

  Divide `compute_unit_seconds` by 3600 for CU-hours. Days with zero
  consumption are omitted from the response entirely.
- Confirm the endpoint's suspend setting is untouched:

  ```bash
  neon api "/projects/<PROJECT_ID>/endpoints"   # expect suspend_timeout_seconds: 0
  ```

- A flat daily value equal to `autoscaling_limit_min_cu x 86400` is the
  signature of a keepalive, and is the first thing to check.

### 7. Wrong vs Correct

#### Wrong

```bash
# Left running between work sessions against a Neon DATABASE_URL.
./scripts/stashseek start --profile full
```

Even a 10-minute repair sweep performs recurring database work; use the read
profile for unattended sessions so Neon can still scale to zero between use.

#### Correct

```bash
# Default development runtime: no Beat, no periodic database traffic.
./scripts/stashseek start --profile read

# Ingestion work is bounded and explicitly torn down.
./scripts/stashseek start --profile full   # ... run the test ...
./scripts/stashseek stop
```

---

## Query Patterns

## Scenario: Project paged parent rows with their latest child row

### 1. Scope / Trigger

Use for bounded lists that need one latest child per parent, such as
conversation threads with their latest completed turn.

### 2. Signatures

```text
page CTE = tenant filters + cursor predicate + stable order + limit + 1
latest child = JOIN page CTE + row_number(partition by parent_id, stable DESC order)
result = page LEFT JOIN latest child WHERE rank = 1
```

### 3. Contracts

- Materialize the tenant-owned parent page before ranking child rows.
- Preserve lexicographic cursor order and the `limit + 1` sentinel.
- Filter child lifecycle before ranking; keep empty parents via `LEFT JOIN`.
- First page uses one query. Cursor pages may add one constant ownership query.
  Query count cannot grow with page size.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| 0 / 1 / maximum rows | Same projection-query count. |
| No eligible child | Return empty-parent fallback. |
| Newer ineligible child | Project latest eligible child. |
| Foreign cursor | Return bounded not-found. |

### 5. Good / Base / Bad Cases

- Good: rank completed turns joined to the paged tenant CTE.
- Base: an empty page still executes one bounded projection.
- Bad: one latest-child query per parent, or rank the entire child table before
  applying the parent page.

### 6. Tests Required

- Count SQL for 0, 1, maximum page size, and cursor pagination.
- Assert ordering, tenant isolation, empty parent, completed-only latest child,
  projection truncation, and next cursor.
- Inspect PostgreSQL SQL/`EXPLAIN`; add an index only from measured evidence.

### 7. Wrong vs Correct

#### Wrong

```python
for thread in threads:
    latest = db.scalar(select(Turn).where(Turn.thread_id == thread.id).limit(1))
```

#### Correct

```python
page = tenant_threads.order_by(updated_at.desc(), id.desc()).limit(limit + 1).cte()
latest = ranked_completed_turns.join(page, page.c.id == Turn.thread_id).subquery()
rows = db.execute(select(page, latest).outerjoin(latest, latest.c.rank == 1))
```

---

## Migrations

<!-- How to create and run migrations -->

(To be filled by the team)

---

## Naming Conventions

<!-- Table names, column names, index names -->

(To be filled by the team)

---

## Common Mistakes

<!-- Database-related mistakes your team has made -->

(To be filled by the team)
