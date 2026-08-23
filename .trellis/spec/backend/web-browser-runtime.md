# Web Browser Runtime Contract

## Ownership and composition

- `app/api/app.py` owns the only browser-facing FastAPI application, including
  authentication, library, conversation, and link routes.
- `app/api/runtime.py` is the production dependency-composition root.
  Compatibility modules such as `app/web_api.py` may delegate to this root,
  but must not define another cookie parser, session resolver, CSRF boundary,
  route set, or error envelope.
- The public prefix is fixed at `/api/v1`. Do not advertise a configurable
  prefix that the canonical routers cannot honor.

## Email authentication contract

- Production browser login is email-only and uses:
  `POST /api/v1/auth/challenges`, `POST /api/v1/auth/verify`, and
  `GET|DELETE /api/v1/auth/session`.
- Keep raw session and CSRF credentials only in `__Host-kb_session` and
  `__Host-kb_csrf`. Unsafe browser requests require exact Origin validation and
  double-submit `X-CSRF-Token` validation.
- Browser errors use the bounded `{code, message}` envelope. Challenge
  acceptance must not distinguish existing, unknown, or rate-limited email
  addresses. Session DTOs expose `authenticated`, `login_channel`, and
  `expires_at`, never tenant, user, identity, or session IDs.

## Scenario: Resolve opaque browser Sessions with bounded Redis caching

### 1. Scope / Trigger

Apply whenever changing browser Session resolution, CSRF, logout/revocation,
account disable/merge, Redis composition, or an authenticated route dependency.

### 2. Signatures

```python
resolve_session(raw_token: str) -> AuthenticatedWebSession
validate_csrf_for_session(session: AuthenticatedWebSession, raw_csrf: str) -> None
mark_web_session_cache_invalidation(db: Session) -> None
```

```dotenv
WEB_SESSION_CACHE_TTL_SECONDS=60  # 0 means PostgreSQL-only
```

### 3. Contracts

- PostgreSQL is authoritative. Cold resolution is one joined query over
  `WebSession`, `AppUser`, and `ChannelIdentity`, enforcing expiry, revocation,
  enabled state, ownership, and the exact `web/web` namespace.
- Redis stores a versioned projection under `generation + SHA-256(token)`.
  Never cache or log raw Session/CSRF credentials. TTL is bounded by both the
  configured TTL and Session lifetime.
- One Redis lookup atomically returns generation and projection. Reject
  unknown/extra fields, invalid types or namespace, naive/expired timestamps,
  malformed CSRF hashes, and payloads over 4 KiB.
- Miss/error falls back to PostgreSQL. A cold fill uses compare-generation
  semantics. After Redis outage or failed invalidation, bump generation before
  accepting future hits and discard possibly stale in-flight fills.
- Middleware stores the validated projection on `request.state`; CSRF and route
  dependencies reuse it. Throttle `last_used_at` instead of writing per request.
- Security mutations mark invalidation in their database transaction, but bump
  Redis only after the outermost commit. SAVEPOINT release is not authoritative.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Valid cache hit | Authenticate without database SELECT. |
| Miss/corrupt/timeout | Run joined authoritative query; never authenticate malformed data. |
| Invalid Session/user/identity | Return bounded `session_invalid`; do not fill Redis. |
| CSRF mismatch | Return bounded `csrf_invalid`; do not resolve twice. |
| Generation changes during cold query | Discard fill. |
| Redis invalidation fails | Database remains authoritative; bump before future hits. |
| Nested commit then outer rollback | Do not invalidate; clear marker on outer rollback. |

### 5. Good / Base / Bad Cases

- Good: one request resolves once, validates CSRF from that projection, and the
  route consumes the same projection.
- Base: TTL `0` uses the joined PostgreSQL resolver with identical security.
- Bad: use Redis as identity authority, cache raw tokens, repopulate an old
  generation after logout, or invalidate on SAVEPOINT release.

### 6. Tests Required

- Assert warm hit = one Redis lookup and zero database SELECTs; cold miss = one
  joined SELECT.
- Cover malformed/oversize payload, expiry, outage/recovery, generation race,
  startup bump, logout/revoke/disable/merge, nested transaction, and TTL `0`.
- Count one Session resolution for unsafe routes and email logout; keep CSRF
  failures fail-closed.

### 7. Wrong vs Correct

#### Wrong

```python
session = resolve_session(raw_cookie)
validate_csrf(raw_cookie, raw_csrf)  # resolves again
cache.set(raw_cookie, session)
```

#### Correct

```python
session = resolve_session(raw_cookie)
request.state.authenticated_session = session
validate_csrf_for_session(session, raw_csrf)
cache.put(token_digest, session, generation=observed_generation)
```

## Tenant and transport boundaries

- Authenticated conversation adapters preserve the resolved tenant's complete
  channel namespace: `channel`, `account_id`, and `external_user_id`. Never
  rebuild a legacy Telegram/WeChat identity as `web/web/<id>` and let
  `ChannelService` self-register a different tenant.
- The combined ASGI dispatcher selects MCP by `MCP_PATH` before dispatching to
  the browser application. Browser cookies never authenticate MCP, and MCP
  Bearer credentials never authenticate browser routes.
- Browser-companion capture is a third, isolated transport credential. Exact
  extension-origin routes use a hash-at-rest `capture:write` Bearer; Web
  cookies approve/list/revoke devices but never authenticate capture, and the
  capture Bearer never authenticates normal Web or MCP routes. See
  `browser-companion-capture.md`.
- Production composition must forward the configured channel service into the
  canonical browser app; retained conversation routes must not become a
  permanent 503 surface through omitted wiring.

## Conversation streaming contract

- The browser SSE route delegates one `ChannelService` execution. Identity
  resolution, message idempotency, and durable turn persistence remain owned by
  that service; the route must not start a second Agent execution or save a
  second response for a compatibility fallback.
- A grounded section is public only after its current-run Citation metadata is
  authorized, then follows `section_started -> text_delta* ->
  section_completed`. Technical interruption may replace completion with
  `section_aborted`; a client disconnect, cancellation, timeout, or incomplete
  section never persists a partial conversation turn.
- Empty or unsupported provider streaming before any section is public falls
  back to one whole-answer `text_delta` plus the final response. If a section
  has already been exposed, the failure is terminal and must not silently
  replay the answer.
- Event fields that do not apply to a lifecycle event are omitted from the JSON
  SSE record when null. Generated TypeScript therefore treats nullable
  event-specific fields such as `section_id`, `status`, and `reason` as
  optional; runtime lifecycle validation remains mandatory.

## OpenAPI and verification

- `scripts/export_web_openapi.py` constructs the real email-enabled production
  route composition with inert dependencies and no provider/network side
  effects. Generated JSON and TypeScript are checked in together.
- Regression tests must cover the production route set, challenge/verification
  safe-error matrix, authenticated library access, CSRF logout, legacy tenant
  affinity through a real `ChannelService`, and combined Web/MCP credential
  isolation.
- Release validation includes the focused credential-free backend workflow,
  the full Python suite with loopback HTTP available, all frontend gates, one
  Alembic head, and a 390×844 real-browser login/logout smoke with empty Web
  Storage and clean page-error/console buffers.
