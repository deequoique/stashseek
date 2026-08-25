# MCP Channel Runtime Contract

## Scenario: Tenant-bound, capability-scoped MCP access

### 1. Scope / Trigger

This contract applies to StashSeek Chat's MCP v2 adapter, its operator-managed
access grants, and both supported transports. MCP is an application channel,
not a fixed service account: every request must resolve a bearer capability to
one stable MCP principal, one `AppUser`, and one `TenantContext` before any
StashSeek Chat service is called.

LangBot remains an optional out-of-process adapter under `integrations/`.
Adding or changing MCP must not introduce LangBot SDK imports, an in-process
plugin registry, or a marketplace dependency under `app/`.

### 2. Signatures

Runtime and operator commands:

```text
python -m app.cli mcp-server --transport stdio
python -m app.cli mcp-server --transport streamable-http

python -m app.cli mcp-grant issue --user-id <id> \
  [--scope read|full] [--expires-at <ISO-8601>] \
  [--label <text>] [--created-by <text>]
python -m app.cli mcp-grant list [--user-id <id>] [--limit 1..100] [--offset 0..1000000]
python -m app.cli mcp-grant show <grant_id>
python -m app.cli mcp-grant rotate <grant_id> [--expires-at <ISO-8601>]
python -m app.cli mcp-grant revoke <grant_id>
python -m app.cli mcp-grant disable <grant_id>
```

Transport entry points:

```python
def run_stdio(server=None, *, settings: Settings | None = None) -> None
def run_streamable_http(server=None, *, settings: Settings | None = None,
                        grant_service=None) -> None
```

The `mcp_access_grant` table stores:

```text
grant_id, app_user_id, token_hash, scope,
expires_at, revoked_at, disabled_at,
created_at, updated_at, rotated_at, last_used_at,
label, created_by
```

The only public MCP tools are:

```text
ask_stashseek
ask_notebook_agent
submit_knowledge_urls
list_saved_items
get_saved_item
update_saved_item
request_delete_saved_items
confirm_item_deletion
cancel_item_deletion
restore_saved_items
retry_item_ingestion
```

### 3. Contracts

- Pin and use the official Python MCP SDK v2 API. Support stdio and Streamable
  HTTP; do not add the obsolete SSE transport or a protocol-neutral fallback
  that can make tests pass while production startup fails.
- `ask_stashseek` enters the existing
  `ChannelService -> KnowledgeAgent` planner/retrieval path. MCP must not expose
  raw search segments, neighbor expansion, citation hydration, storage access,
  tenant IDs, dispatch IDs, model configuration, or purge controls.
- A raw token contains at least 256 random bits. Persist only its SHA-256 hash.
  Display raw token material once on issue or rotation; list, show, disable,
  revoke, logs, errors, and metadata output must never contain it.
- `grant_id` is the stable, non-secret external MCP principal. Resolution is:

  ```text
  token hash -> active grant -> scope -> ChannelIdentity(channel="mcp")
             -> active AppUser -> TenantContext
  ```

  Tool schemas never accept `app_user_id`. Multiple grants may map to one user
  with different scopes, and grants may map to different users.
- `expires_at` defaults to `NULL`. Grants survive restarts and inactivity until
  explicit rotation/revocation/disablement, optional expiry, identity disable,
  or `AppUser` disablement.
- `ask_notebook_agent` is a compatibility alias that uses the same input/output
  models, facade implementation, tenant resolution, and evidence path as
  `ask_stashseek`; it must not fork business logic.
- `read` discovery contains exactly `ask_stashseek`, `ask_notebook_agent`,
  `list_saved_items`, and `get_saved_item`. `full` may expose all eleven tools only
  when the mutation readiness assessment succeeds. Discovery and invocation
  both fail closed.
- Production mutation readiness is bounded and covers database, broker,
  object store, maintenance configuration, and Celery worker availability.
  Worker readiness requires a pong and active `ingest` plus `maintenance`
  queues. Missing, malformed, exceptional, or timed-out probes are unavailable,
  never implicitly ready.
- Stdio requires `MCP_TOKEN` and resolves its scope before registering tools.
  Stdout contains protocol bytes only; diagnostics use stderr or the bounded
  private log sink.
- A parent process that captures stdio-server diagnostics in a seekable file
  must open the child stderr target with append semantics. Parent reads must
  not move the subprocess's shared write offset backwards. The capture file
  must outlive transport shutdown so final diagnostics can be drained, while
  shutdown remains hard-bounded and run-owned grants are still revoked when
  transport cleanup fails or times out.
- Streamable HTTP prefers `Authorization: Bearer <token>`. Optional MiXer
  compatibility accepts `https://host/<MCP_PATH>/c/<opaque-token>` only when
  `MCP_URL_TOKEN_MODE=true`. Query tokens are rejected, HTTPS is required for
  path tokens, and `MCP_PATH` must be a non-root absolute path without a query,
  fragment, or trailing slash.
- Streamable HTTP keeps the official SDK DNS-rebinding protection enabled.
  Loopback hosts remain admitted; when the combined authenticated Web runtime
  is enabled, its already validated exact `WEB_PUBLIC_ORIGIN` host and origin
  are added to the MCP transport allowlist. Never disable host validation or
  trust an arbitrary forwarded host to make a reverse proxy work.
- URL capability paths are still secrets: MiXer, proxies, and infrastructure
  can see the full URL. Redact or omit the original URI before application,
  error, access, or analytics logging, and rotate after suspected disclosure.
- MCP management markers are server-owned ordering records. Exclude them
  before applying the model-history turn cap so inventory calls cannot evict
  useful conversation history. A fresh delete request returns a confirmation
  code only after its marker is durably persisted; marker failure cancels or
  invalidates the pending action and returns a code-free safe failure.

Environment keys:

```text
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=false
MCP_TOKEN=<stdio-only raw bearer, process environment>
```

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Missing, malformed, expired, rotated, revoked, or disabled token | Return stable `invalid_grant` / HTTP 401 without existence or tenant detail |
| Disabled MCP identity or disabled `AppUser` | Reject as an invalid grant |
| `read` grant invokes or discovers a mutation | Omit it from `tools/list`; fail closed if invoked by name |
| Required mutation dependency or worker unavailable | Withhold mutation tools and return bounded `mutation_unavailable` outcomes |
| `?token=...` or percent-encoded query token | Reject; never normalize it into accepted auth |
| URL token mode disabled, non-HTTPS request, or path mismatch | Reject without echoing the request URI or token |
| `MCP_PATH` is `/`, relative, has query/fragment, or trails with `/` | Configuration error before serving |
| Host/Origin is outside loopback and the validated public Web origin | Reject with HTTP 421 before protocol handling |
| Raw token is empty or longer than the accepted bound | `invalid_grant` |
| Grant scope, expiry, label, creator, limit, or offset invalid | Stable bounded operator error; no raw SQL/provider detail |
| Delete marker cannot be persisted | Cancel best-effort, return `management_unavailable`, and return no confirmation code |
| Slash command or invalid tool schema reaches MCP | Reject before planner invocation |
| Provider/service failure | Return a bounded public status/error code; never provider bodies, storage keys, or model traces |

### 5. Good / Base / Bad Cases

- Good: issue separate `read` and `full` grants for the intended users; a read
  client discovers three tools, a ready full deployment discovers ten, and an
  official MCP call reaches the existing tenant-scoped Agent path.
- Base: omit `expires_at` for a normal long-lived MiXer grant, use Bearer auth,
  and rotate/revoke operationally. Inactivity alone never expires it.
- Bad: configure one global `AppUser`, pass `app_user_id` as a tool argument,
  persist the raw token as `external_user_id`, accept `?token=...`, log the
  capability URL, assume an absent Celery probe is healthy, or test only a fake
  facade/fallback. Each bypasses tenant, secret, readiness, or protocol proof.

### 6. Tests Required

- Use the official MCP client to assert `initialize -> tools/list -> tools/call`
  for read and full profiles and to inspect the exact typed/bounded wire schemas.
- Drive both `ask_stashseek` and `ask_notebook_agent` through that client into a real `ChannelService`
  and controlled `KnowledgeAgent` `FunctionModel`; assert a planner call for
  natural language and zero calls for slash commands and invalid schemas.
- Start a real stdio subprocess and assert protocol-clean stdout, token/scope
  resolution, discovery, and representative invocation.
- Cover stdio capture shutdown: final diagnostics emitted during transport
  close remain readable, the capture file is closed, teardown is bounded, and
  cancellation propagates while run-owned grant revocation is still attempted.
- Exercise the Streamable HTTP ASGI app for missing/malformed/read/full/revoked
  credentials, Bearer/path parity, HTTPS enforcement, query rejection, custom
  paths, session initialization, discovery, calls, exact public-host admission,
  unknown-host rejection, and secret-free failures.
- Cover grant hashing, one-time token output, no-expiry persistence,
  multi-user/multi-grant scope mapping, rotation, revocation, disablement,
  expiry, disabled identity/user, pagination bounds, and restart
  re-instantiation.
- Test every readiness probe as ready, missing, malformed, exceptional, and
  timed out. Assert worker pong and both required queues before mutation tools
  appear.
- Use real conversation and pending-action persistence for a fresh MCP delete
  request/confirm flow. Cover marker write failure and prove more management
  markers than the history cap do not evict real model turns. Shared pending
  tests must retain replacement, replay, cancellation, expiry, and effect-race
  coverage.
- Run the full suite with PostgreSQL and loopback HTTP available before release.
  Real MiXer validation must use the exact capability URL through initialize,
  `tools/list`, session behavior, and `tools/call`.

### 7. Wrong vs Correct

#### Wrong

```python
# One fixed tenant for every remote caller; token also leaks into identity.
tenant = TenantContext(app_user_id=settings.mcp_user_id)
external_user_id = request.query_params["token"]
server = FakeMcpServer()  # tests pass without the official transport
```

```python
# Unknown worker state is treated as healthy.
worker_ready = True if worker_probe is None else worker_probe()
```

#### Correct

```python
grant = grant_service.resolve(raw_bearer)
tenant = grant.tenant  # derived from stable grant principal and active identity
server = create_mcp_server(scope=grant.scope, mutation_ready=readiness.ready)

# Every required readiness signal is explicit and bounded; unknown fails closed.
readiness = assess_mcp_mutation_readiness(
    settings,
    worker_probe=probe_mcp_worker,
)
```

Use the official MCP v2 server/client APIs in both production and tests, and
keep raw bearer material out of persistence, identities, schemas, and logs.
