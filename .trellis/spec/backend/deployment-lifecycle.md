# Deployment Lifecycle

StashSeek Chat exposes a profile-aware launcher at `scripts/stashseek`.
It is the preferred single-host deployment entry point; the direct Python,
Celery, and Docker Compose commands remain supported for advanced operators.
`scripts/notebook-agent` is a compatibility wrapper and must continue to
forward arguments, signals, and exit status to the canonical launcher.

## Runtime profiles

- `read` starts Streamable HTTP MCP and requires PostgreSQL. It must not require
  or start Redis, MinIO, a Celery worker, or Celery Beat.
- `full` starts MCP, the loopback-only LangBot gateway, one Celery worker
  consuming both `ingest` and `maintenance`, and exactly one Beat scheduler. It
  requires PostgreSQL, Redis, object storage, and a channel gateway secret.
- `langbot` starts the full background runtime and the loopback-only LangBot
  gateway. It does not start the public MCP endpoint by default.

Adding a profile or component requires updating the immutable plan in
`app/deployment.py`, its readiness and shutdown behavior, focused CLI tests,
and both deployment guides.

## Configuration ownership

The launcher writes only non-default profile choices and required secrets to
the ignored `.env.runtime` file. The file must use mode `0600`. Do not generate
a copy of the exhaustive `.env.example` catalog.

Effective precedence is:

1. process environment;
2. operator-owned `.env`;
3. launcher-owned `.env.runtime`;
4. application defaults.

The launcher must never overwrite `.env`, log credential values, or expose
migration-only credentials to long-running children. Redaction must cover
explicit secret settings and credential-bearing URLs.

## Lifecycle and ownership

The launcher presents all selected components as one managed runtime while
keeping worker and Beat in separate OS processes. A reservation plus run ID
prevents concurrent supervisors and duplicate Beat instances. PID-only checks
are insufficient: lifecycle commands must validate the recorded process
identity before signaling it.

Startup order is dependencies, migration, then every application child in the
selected profile. Do not place a deep dependency probe between child launches:
`full` must launch worker, Beat, MCP, and gateway as one operation. Every
selected application listener is a post-launch readiness gate, and a profile
with multiple listeners must assign them distinct ports so one process cannot
satisfy another component's socket probe. All pre-start
checks and subprocesses must have bounded timeouts, and the outer launcher and
stop budgets must exceed the corresponding listener and cleanup budgets. A
child exit stops the owned runtime immediately. Dependency probe failures
are reported by on-demand `status` checks but are not lifecycle signals:
transient or slow external checks must never block the supervisor loop or tear
down otherwise-live owned processes. Shutdown must include process groups and
Celery descendants without signaling unrelated processes.

On-demand status checks must verify that their effective non-secret service
targets match a fingerprint captured by the running supervisor. If an
operator's one-shot environment override is no longer present or points to a
different database, broker, object store, Compose project, or listener, status
must report configuration mismatch instead of probing the wrong runtime.

External PostgreSQL, Redis, and object storage are readiness-checked but never
stopped or mutated. Compose services may be rolled back only when the current
start attempt can prove ownership. External object-store bucket checks are
read-only; bucket creation is limited to launcher-owned local Compose MinIO.

## Safety gates

- Run the single Alembic head before starting application children.
- Neon pooled runtime URLs require TLS and a direct migration URL targeting
  the same host family and database.
- Non-loopback MCP binding requires the explicit
  `STASHSEEK_ALLOW_NON_LOOPBACK=true` acknowledgement. The former
  `NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK` key remains a compatibility fallback.
- Validate custom CA bundles before starting infrastructure or children.
- Keep health and status snapshots redacted and bounded in size.

## Quality checks

Changes to the lifecycle must run at least:

```bash
python -m pytest -q tests/test_deployment_cli.py \
  tests/test_production_caddy_deployment.py tests/test_mcp_server.py \
  tests/test_tasks.py tests/test_ingest_notifications.py
sh -n scripts/stashseek scripts/notebook-agent
git diff --check
```

Tests should use injected or monkeypatched runners and must not require real
provider credentials or mutate external services.

## Forbidden patterns

- Starting worker and Beat as one process or allowing more than one Beat.
- Killing by process name, unvalidated PID, or broad wildcard.
- Passing `MIGRATION_DATABASE_URL` to long-running processes.
- Copying all example variables into generated runtime configuration.
- Creating buckets or stopping services that the launcher does not own.
- Logging raw subprocess output without secret redaction.

## Scenario: Shared-host production Web, API, MCP, and background runtime

### 1. Scope / Trigger

- Trigger: a production release serves the authenticated SPA, `/api/v1/*`, and
  Streamable HTTP MCP on an existing host that already runs unrelated services.
- This contract applies to `deploy/systemd/`, `deploy/compose/`,
  `deploy/caddy/`, and the production deployment scripts. It does not replace
  the profile-aware local launcher contract above.
- The deployment owns only the `notebook-agent` systemd units, the
  `notebook-agent` Compose project and volumes, `/opt/notebook-agent`,
  `/etc/notebook-agent`, `/var/lib/notebook-agent`,
  `/var/log/notebook-agent`, `/opt/notebook-agent/shared/langbot`, the
  dedicated `notebook-langbot` service identity, and the dedicated Caddy site
  block.

### 2. Signatures

- Combined public runtime:
  `python -m app.cli mcp-server --transport streamable-http`, bound to
  `127.0.0.1:8800`. Email-enabled production must not run a parallel
  `python -m app.cli web-server`; the MCP command owns the SPA, API, and MCP
  routes in one ASGI process.
- Worker:
  `python -m celery -A app.ingest.tasks.celery_app worker --queues=ingest,maintenance`.
- Scheduler:
  `python -m celery -A app.ingest.tasks.celery_app beat` with deployment-owned,
  unique PID and schedule paths.
- Restricted deploy command: `deploy <sha>`, where `<sha>` is exactly 40
  lowercase hexadecimal characters and equals the current `origin/main`.
- Caddy site: `notebookai.deequoique.tech` reverse proxies only to
  `127.0.0.1:8800`.
- Production full runtime additionally owns a loopback Channel Gateway at
  `127.0.0.1:8765` and patched LangBot 4.10.6 WebUI/API at
  `127.0.0.1:5300`. LangBot administration uses an SSH tunnel, never Caddy.

### 3. Contracts

- Runtime environment requires `WEB_AUTH_ENABLED=true`,
  `WEB_PUBLIC_ORIGIN=https://notebookai.deequoique.tech`,
  `WEB_COOKIE_SECURE=true`, `MCP_PATH=/mcp`, `MCP_URL_TOKEN_MODE=true`, and a
  writable `STASHSEEK_LOG_DIR=/var/log/notebook-agent`. This deployment's
  URL-only evaluator uses the HTTPS `/mcp/c/<token>` capability path; query
  tokens remain rejected and the dedicated Caddy site discards access logs.
- The combined MCP transport must retain SDK DNS-rebinding protection while
  admitting the exact validated `WEB_PUBLIC_ORIGIN` host through Caddy.
- `DATABASE_URL` is the pooled external PostgreSQL runtime URL.
  `MIGRATION_DATABASE_URL` is the matching direct URL and is exposed only to
  the one-shot migration unit.
- Redis publishes only `127.0.0.1:16379`; MinIO publishes only
  `127.0.0.1:19000` and `127.0.0.1:19001`. Neither service may bind a public
  interface, and production Compose must not start PostgreSQL.
- Dependency admission requires healthy Redis, healthy MinIO, and successful
  creation or confirmation of the deployment-owned `MINIO_BUCKET` (default
  `kb-raw`). A green MinIO liveness endpoint alone is not sufficient.
- Shared-host Caddy integration is additive: preserve existing configuration,
  validate the complete candidate configuration, install only the dedicated
  hostname block, gracefully reload Caddy, and recheck existing routes.
- Every systemd application unit that can emit file logs must set the writable
  log directory and grant that directory through its sandbox. Secrets remain
  in root-owned mode-`0600` environment files and must not appear in logs.
- The LangBot unit runs under a dedicated service identity, loads only the
  SHA-256-verified patched package, and starts after Gateway health. Its
  required bridge plugin must initialize before the sole Telegram adapter may
  start. WeChat/OpenClaw is not installed or configured in this rollout.
- Upstream LangBot 4.10.6 binds its API to all interfaces; production must use
  the versioned patch that forces `127.0.0.1`, and validation must prove the
  public server address cannot reach port 5300.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| `8800`, `16379`, `19000`, or `19001` is already owned | Stop before mutation and report the conflict. |
| Requested SHA is malformed or differs from `origin/main` | Reject the deploy without switching `current` or restarting units. |
| Alembic has multiple heads or migration admission fails | Do not start the release; retain or restore the previous `current`. |
| Redis or MinIO is unhealthy | Dependency unit fails; application units remain stopped. |
| MinIO is live but bucket admission fails | Dependency unit fails; do not treat liveness as readiness. |
| Complete Caddy candidate does not validate | Keep the original configuration and do not reload. |
| New route fails after reload | Restore the backed-up Caddy configuration and gracefully reload it. |
| An unrelated pre-existing route changes behavior | Roll back only the StashSeek Chat site change and investigate. |
| `STASHSEEK_LOG_DIR` is absent or unwritable | Unit startup fails; do not weaken the systemd filesystem sandbox. |
| Post-switch health check fails | Restore the previous immutable release and restart only owned units. |

### 5. Good/Base/Bad Cases

- Good: the external database is reachable, loopback ports are free, the
  owned bucket exists, migrations pass, and HTTPS serves the SPA, API, and
  `/mcp` through the combined process while existing host routes are unchanged.
- Base: repeated deployment of the current clean `main` SHA is serialized and
  idempotent; the owned Compose volumes and bucket remain intact.
- Bad: starting both `web-server` and `mcp-server`, accepting MinIO health
  without the bucket, binding Redis or MinIO to `0.0.0.0`, replacing the whole
  Caddyfile without validation, or restarting/killing unrelated services.

### 6. Tests Required

- Static deployment tests assert the combined runtime contains `mcp-server`,
  excludes `web-server`, binds MCP to loopback, enables Web auth, and enables
  only the HTTPS MCP capability-path mode (never query tokens).
- Compose tests assert there is no PostgreSQL service, every published
  dependency port starts with `127.0.0.1`, Redis persistence/auth are enabled,
  and `minio-init` admits the configured bucket.
- Systemd tests assert worker queues, the unique Beat state paths, migration
  gating, `STASHSEEK_LOG_DIR`, writable log paths, and separation of
  runtime versus migration credentials. Gateway/LangBot tests additionally
  assert loopback listeners, dedicated ownership, patched package admission,
  required bridge readiness, and no WeChat adapter configuration.
- Caddy tests assert only the StashSeek Chat hostname and loopback upstream are
  present. Operational validation must run `caddy validate` against the full
  candidate configuration before a graceful reload and probe old routes after.
- Deploy-script tests assert strict SHA parsing, `origin/main` equality,
  serialization, targeted unit restarts, rollback to the previous release, and
  absence of data-destructive commands such as `docker compose down`.

### 7. Wrong vs Correct

#### Wrong

```ini
ExecStart=/opt/notebook-agent/current/.venv/bin/python -m app.cli web-server
# A second public MCP process is then started on another port.
```

```yaml
ports:
  - "6379:6379"
```

#### Correct

```ini
Environment=STASHSEEK_LOG_DIR=/var/log/notebook-agent
Environment=MCP_HOST=127.0.0.1
Environment=MCP_PORT=8800
ExecStart=/opt/notebook-agent/current/.venv/bin/python -m app.cli mcp-server --transport streamable-http
ReadWritePaths=/var/log/notebook-agent
```

```yaml
ports:
  - "127.0.0.1:16379:6379"
```

## Scenario: Brand-compatible runtime configuration

### 1. Scope / Trigger

- Trigger: a product or repository rename changes developer-facing commands or
  environment keys while existing local and production installations must keep
  working without moving state.

### 2. Signatures

```text
scripts/stashseek <lifecycle command>       # canonical
scripts/notebook-agent <lifecycle command> # compatibility wrapper

_compat_value(env, canonical, legacy, default)
_canonicalize_compat_layer(values)
```

### 3. Contracts

- Source precedence is process canonical → process legacy → operator `.env`
  canonical → operator legacy → managed `.env.runtime` canonical → managed
  legacy → default.
- Normalize the alias inside each source layer before merging layers. Never
  flatten all sources first and then prefer the canonical spelling.
- New docs and generated configuration lead with `STASHSEEK_*`; legacy
  `NOTEBOOK_AGENT_*` keys remain accepted during the compatibility window.
- Installed `notebook-agent*.service` names and `/opt`, `/etc`, `/var/lib`,
  `/var/log/notebook-agent` paths are stable operational identifiers. A brand
  rename does not authorize moving them.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| process canonical and process legacy both set | canonical wins |
| process legacy and lower-layer canonical set | process legacy wins |
| operator legacy and managed canonical set | operator legacy wins |
| only legacy key set | same validation and behavior as canonical key |
| invalid canonical/legacy value | stable canonical-key validation error; no value logging |

### 5. Good / Base / Bad Cases

- Good: a new `STASHSEEK_PROFILE=read` process override wins and the old
  launcher starts the same plan through its wrapper.
- Base: an existing `.env.runtime` containing only `NOTEBOOK_AGENT_PROFILE`
  continues to start unchanged.
- Bad: merge files and process into one dictionary and then select
  `STASHSEEK_PROFILE`; a lower-precedence file can silently defeat a process
  legacy override.

### 6. Tests Required

- Cover canonical-only, legacy-only, same-layer conflict, and every cross-layer
  conflict with exact selected values.
- Assert both launcher files are executable and shell-valid.
- Keep production static tests pinned to stable unit/path identities while
  asserting canonical `STASHSEEK_*` unit environment values.

### 7. Wrong vs Correct

#### Wrong

```python
resolved = {**managed, **operator, **process}
profile = resolved.get("STASHSEEK_PROFILE", resolved.get("NOTEBOOK_AGENT_PROFILE"))
```

#### Correct

```python
resolved = {}
for layer in (managed, operator, process):
    resolved.update(_canonicalize_compat_layer(layer))
profile = resolved["STASHSEEK_PROFILE"]
```
