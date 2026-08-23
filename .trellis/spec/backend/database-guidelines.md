# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

Notebook Agent uses SQLAlchemy and Alembic with PostgreSQL. Production runtime
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
