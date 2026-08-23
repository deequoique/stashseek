# Browser authentication performance options

## Decision context

Notebook Agent is a same-origin browser application with one resource server,
server-owned tenant identity, explicit logout/session revocation, account and
channel-identity disablement, and a production-local Redis instance. The
observed latency comes from repeated remote Neon round trips, not from the
cryptographic cost of hashing an opaque cookie.

Current authenticated reads perform separate session, user and identity reads
plus a `last_used_at` update. Protected writes resolve the same session once in
the CSRF boundary and again in the route dependency.

## Option A: stateless JWT access tokens

### Benefit

- Signature verification avoids the normal database lookup.
- Useful when many independent resource servers need to validate claims
  without a shared session store.

### Cost for this product

- Logout, session revocation, disabled users, disabled identities and account
  merges cannot take effect immediately without a denylist/status lookup,
  short access-token lifetime, or an authorization-version lookup.
- Cookie-carried JWTs still need the existing Origin and CSRF protections.
- Refresh tokens reintroduce server-side state and rotation/replay handling.
- Tenant and identity claims become stale or require a lookup when account
  linking changes ownership.
- Key rotation, strict algorithm/audience/issuer validation and claim-version
  compatibility add a new security surface without addressing conversation
  N+1 queries.

OWASP's current JWT guidance explicitly says JWTs are often suggested for
“stateless” sessions but that usage is frowned upon, and documents status
lists/denylists when revocation is needed:
https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html

RFC 7519 defines JWT as a compact protected claims format; it does not provide
server-side revocation semantics:
https://www.rfc-editor.org/rfc/rfc7519.html

**Conclusion:** not recommended for the Notebook Agent browser session.

## Option B: optimized database-backed opaque session

### Shape

- Keep the current random opaque cookie and hash-only database storage.
- Resolve session + user + channel identity with one joined query.
- Put the result on `request.state` so CSRF validation and route dependencies
  reuse one resolution.
- Validate CSRF against the same resolved session projection.
- Throttle `last_used_at` writes rather than updating on every request.

### Trade-off

- Preserves immediate database revocation and keeps the smallest change.
- Still pays at least one remote Neon round trip per request; this may be good
  enough after measurement but cannot offer local-cache latency.

## Option C: opaque session with Redis read-through cache

### Existing infrastructure

- Production already requires a loopback Redis container through
  `notebook-agent-dependencies.service`.
- `redis==8.1.0` is already a mandatory Python dependency.
- Email rate limiting and Celery already consume the configured `REDIS_URL`.
- FastAPI already has one `secure_web_boundary` middleware and uses
  `request.state` for request-scoped data.

No new external service, package, sidecar or second HTTP middleware is needed.
The implementation needs an internal cached-session resolver plus changes to
the existing boundary/dependency so one resolved session is stored on and
reused from `request.state`.

### Shape

- Apply all Option B changes first.
- Keep PostgreSQL authoritative and the browser cookie opaque.
- Cache only the bounded resolved-session projection under a token-digest key
  in the production-local Redis, never the raw cookie token.
- Bound cache TTL by session expiry and a short revocation-safety TTL.
- Invalidate the exact session on logout/revoke and all user session keys on
  disable/account-security operations.
- On cache miss, use the one joined PostgreSQL query and repopulate Redis.
- On Redis failure, fall back to the authoritative database rather than
  accepting an unverified session.
- Reuse the resolved projection through the whole request so protected writes
  do not perform another lookup.

### Trade-off

- Removes Neon from the hot request path in normal production traffic.
- Requires explicit cache invalidation tests and bounded behavior for Redis
  outages, revocation races and account/identity disablement.

OWASP recommends meaningless session IDs with application meaning stored
server-side and server-side expiration/invalidation:
https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

**Conclusion:** recommended production target, implemented on top of Option B
rather than as a replacement for database authority.

## Recommended staged decision

1. Do the structural fixes that are required regardless of cache choice:
   single joined authorization read, request-scoped reuse, CSRF reuse and
   throttled audit writes.
2. Add Redis read-through caching because production already owns a loopback
   Redis dependency and the reported environment is not usable with Neon on
   every request.
3. Keep PostgreSQL authoritative and retain all current cookie, tenant,
   expiry, revocation, disablement and CSRF boundaries.
4. Do not introduce JWT/refresh-token machinery for this task.
