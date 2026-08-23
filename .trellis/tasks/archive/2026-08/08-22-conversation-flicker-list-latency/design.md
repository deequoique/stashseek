# Technical Design

## 1. Scope and invariants

This task changes three boundaries while preserving their owners:

1. React owns the temporary stream projection and TanStack Query owns persisted conversation DTOs.
2. The conversation API owns tenant-scoped list projection and pagination.
3. PostgreSQL owns browser Session truth; Redis accelerates resolution but cannot authorize independently of a previously validated projection.

The implementation must not expose database IDs, channel message IDs, raw Session/CSRF tokens, email addresses, Redis keys, or provider data to browser DTOs or logs.

## 2. End-to-end flow

```text
Browser opaque cookies
  -> existing secure_web_boundary
     -> request-scoped Session resolver
        -> Redis generation + digest lookup
           -> hit: bounded Session projection
           -> miss/error: one joined PostgreSQL resolution
                          -> generation-safe Redis fill
        -> request.state resolved Session
     -> exact-Origin + double-submit CSRF for unsafe methods
  -> route dependency reuses request.state
  -> conversation list uses one set-based page/latest-turn query
  -> React updates sidebar in background
```

## 3. Authentication design

### 3.1 Canonical projection

Introduce one internal immutable resolved projection containing only what existing routes need:

- Session database/public identity and expiry;
- app-user ID and channel-identity ID;
- channel namespace required by `TenantContext`;
- CSRF token hash;
- cache generation and last-used timestamp as internal metadata.

Raw cookie values are accepted only at the request boundary, immediately digested, and never stored in the projection, Redis value, diagnostics or exception text.

### 3.2 Cold database resolution

Replace separate `WebSession`, `AppUser` and `ChannelIdentity` reads with one joined `SELECT` that enforces in SQL:

- token digest equality;
- Session not revoked and not expired;
- linked identity exists, belongs to the same user, is the `web` identity and is not disabled;
- user exists and is not disabled.

No permissive fallback may reconstruct a tenant from browser data. A missing or inconsistent row remains `session_invalid`.

`last_used_at` becomes audit metadata updated on cold/cache-expired resolution only, bounded by the cache TTL rather than written on every API request. Its failure must not make an otherwise valid auth decision less safe or leak database details.

### 3.3 Redis cache

Add an internal protocol with `resolve`, `store`, `invalidate_all_generation` and bounded health/failure behavior. The production implementation uses the existing synchronous Redis client and namespace-isolated keys.

- Cache key: fixed namespace + current generation + SHA-256 token digest.
- Cache value: versioned JSON projection; strict decode rejects unknown version, wrong types, expired entries, invalid channel namespace or missing CSRF hash.
- TTL: `min(configured short TTL, Session remaining lifetime)`; default target 60 seconds.
- Hit validation: one bounded Redis operation reads the current generation and matching value atomically or via a single server-side script/pipeline.
- Miss: use the joined database resolver, then fill only if the generation observed before the database read is still current.
- Redis timeout/corruption: treat as miss, never as authenticated; fall back to PostgreSQL.

### 3.4 Cache generation and invalidation

Use one global cache generation for the MVP. This deliberately trades occasional broad cold starts for simple fail-closed invalidation.

- Increment generation at application startup before serving cache hits, so persisted entries from an earlier process are stale.
- After successful database logout/revoke, user disable, absorbed-account Session revocation or identity merge, increment generation.
- If Redis is unavailable, continue from PostgreSQL and mark the cache client as requiring a generation bump. The first successful Redis interaction bumps generation before accepting hits.
- A concurrent cold fill may write only under the generation it observed; a later invalidation generation makes it unreachable.

All credential-mutating paths must call one invalidation owner after the authoritative database commit. Tests cover the Web logout route, CLI user disable, channel/web account merge, ordinary expiry and explicit Session revoke.

### 3.5 Request-scoped reuse and CSRF

Do not add a second HTTP middleware. Extend the existing secure boundary and canonical dependency around one helper:

```text
resolve_request_session(request):
  if request.state already owns a validated projection -> return it
  otherwise resolve through cache/database -> store -> return
```

Unsafe requests keep exact Origin, CSRF cookie/header constant-time equality and server-side CSRF hash verification. Once the boundary succeeds, the downstream route receives the same projection and performs no second Session resolution.

Authentication endpoints that are outside the unsafe middleware reuse the same resolver/cache service explicitly where appropriate; login creation remains a database write and may warm the cache only after commit.

## 4. Conversation-list query

Replace the per-thread loop with a set-based statement:

1. Select the tenant-owned thread page ordered by `(updated_at DESC, id DESC)` with `limit + 1`.
2. Derive the latest `status='completed'` turn per page thread using a window-ranked or equivalent set-based subquery.
3. Outer join the latest turn so empty conversations still produce `新对话` with an empty preview.
4. Preserve cursor ownership and lexicographic pagination. Cursor lookup may remain one additional constant query.

Required query-count contract:

- first page: constant one list-projection query;
- cursor page: constant cursor-ownership query plus one list-projection query;
- count does not change between 0, 1 and 30 returned threads.

The existing `(thread_id, created_at DESC)` turn index and tenant thread ordering index remain in use. Add a migration only if `EXPLAIN` shows a missing predicate/order index; do not add speculative indexes.

## 5. Frontend state handoff

### 5.1 Completed answer

The terminal `completed.response` remains the authoritative pending projection until the selected transcript refetch has populated the durable replacement.

```text
terminal event paints final answer
  -> mutation resolves
  -> conversation-list invalidation starts in background
  -> selected transcript invalidation/refetch is awaited for handoff only
  -> pending projection clears after durable query data is installed
```

Do not clear `pendingQuestion`, answer, citations, sections, steps or plan before transcript convergence. The handoff must be batched so no frame contains neither representation and the final frame contains only the persisted turn. A transcript refetch failure keeps the safe terminal projection visible and exposes a retryable sync error rather than blanking it.

### 5.2 New conversation

After reset returns:

- set the new public thread ID and client-generated conversation ID immediately;
- end mutation pending state without awaiting the sidebar list;
- update the cached sidebar with a safe `新对话` placeholder when possible;
- invalidate/refetch the list in the background for canonical ordering/time;
- allow the composer as soon as reset succeeds, independent of list latency.

## 6. Compatibility and API surface

- No JWT, new cookie, browser storage, auth header or new public endpoint.
- No OpenAPI response-shape change is expected.
- Legacy channel-authenticated and combined Web/MCP compositions retain their credential separation.
- Development can run without an available Redis cache and remains correct through PostgreSQL; production reuses the existing loopback Redis dependency.

## 7. Observability and privacy

Add bounded metrics/log fields only for cache outcome (`hit|miss|error`), resolution source, query count in tests, and safe duration. Never log token digests, Redis keys/values, email, user/identity IDs, cookies, CSRF values, DSNs or exception messages in production.

## 8. Rollout and rollback

1. Land correctness tests before behavior changes.
2. Deploy with Redis cache TTL configurable and `0` meaning database-only mode for emergency rollback.
3. Verify cold database resolution, warm cache resolution, list SQL count and real HTTPS timing.
4. If cache behavior is suspect, set TTL to `0`; the joined database resolver and request-scoped reuse remain active.
5. Rollback requires no data migration unless evidence demanded a new index; Redis entries are disposable and generation-versioned.
