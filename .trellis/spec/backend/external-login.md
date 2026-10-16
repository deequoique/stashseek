# External Website Login

## 1. Scope / Trigger

Apply when changing `app/external_auth.py`, `app/external_providers/`, browser
authentication routes/composition, login-related schema, or account ownership
operations. Website Microsoft, WeChat and QQ login share the canonical Web
session authority; messaging/Bot `wechat` remains a different identity.

This is an implemented code contract. It does not establish approved provider
applications, real mail delivery, production deployment, or live-provider smoke.
All provider enable flags default to false. Registration instructions are in
`docs/how-to/configure-external-login.md`.

## 2. Signatures

```python
ExternalLoginService(email_auth: WebAuthService, settings, providers)
WebAuthService.verify(email, code, *, now=None, external_login_flow_token="")
start(provider, *, client_ip="unknown", previous_flow_token="", session_token="", now=None)
callback(provider, *, raw_flow_token, state, code="", error="", session_token="", now=None)
pending(raw_flow_token, *, session_token="", now=None)
request_email_challenge(raw_flow_token, email, client_ip, *, now=None)
verify_email(raw_flow_token, email, code, *, confirm_binding, session_token="", now=None)
validate_flow_csrf(raw_flow_token, raw_csrf, *, now=None)
cancel(raw_flow_token, *, now=None)

ExternalProvider.begin(*, redirect_uri, state) -> ProviderAuthorization
ExternalProvider.complete(*, redirect_uri, code, material) -> ExternalPrincipal
```

| HTTP operation | Request / result |
| --- | --- |
| `POST /api/v1/auth/external/{provider}/start` | Native form/navigation, exact Origin; 303 to a fixed provider endpoint or clean `/login/external` failure page. |
| `GET /api/v1/auth/external/{provider}/callback` | Bounded, unique code/state/error parameters; 303 to `/library` or `/login/external`. |
| `GET /api/v1/auth/external/pending` | `{status, provider, expires_at, error_code}` only. |
| `POST /api/v1/auth/external/pending/challenges` | Strict `{email}`; normal/rate-limited requests return `{status:"accepted"}`. |
| `POST /api/v1/auth/external/pending/verify` | Strict `{email,code,confirm_binding:true}`; identifier-free `SessionResponse`. |
| `DELETE /api/v1/auth/external/pending` | Cancel and expire flow cookies; 204. |

Provider enum: `microsoft | wechat_website | qq`. Pending statuses:
`created | exchanging | email_required | completed | failed | cancelled | expired`.
`/api/v1/capabilities.external_login_providers` is independent of the existing
`web_login_channels`; `SessionResponse.login_channel` additionally accepts the
three external methods.

Database revision `e2f3a4b5c6d7` follows `d04fdae36884` additively:

- `ExternalLoginIdentity`: non-null unique
  `(provider, application_id, namespace, subject)`, owner and exact email anchor,
  soft-disabled state; restrictive owner deletion and deferred composite
  `(channel_identity_id, app_user_id)` FK preserve ownership during merge.
- `ExternalLoginFlow`: hash-only browser token/state/CSRF, provider configuration
  scope, lifecycle/expiry, encrypted transient SDK material, and completed
  session hash for safe result reconciliation.
- `WebAuthChallenge.external_login_flow_id`: null for ordinary email proof;
  non-null for one exact binding flow.
- `WebSession.external_login_identity_id`: null for compatible email/legacy
  sessions, restrictive FK for provider provenance. Never use `SET NULL` to
  convert a provider session to email authentication.

## 3. Contracts

- `AppUser.id` remains the library owner; the tenant is the exact verified
  `web/web/<canonical-email>` identity. First authorization creates neither a
  standalone social tenant nor an authenticated session. Only that flow's
  verified email plus explicit `confirm_binding:true` can bind the external key.
  A provider email, nickname, avatar, UnionID or Bot identifier never selects an
  existing owner. Preserve subject case and non-empty namespace values.
- Shared transaction-local email helpers create candidate user/identity rows
  **inside** a SAVEPOINT. Unique-key losers discard candidates and re-read the
  winner. Different owner/anchor or disabled mapping is a conflict, never an
  overwrite. Consume successful proofs, mapping and session in one commit; set
  cookies only afterwards. Wrong-code attempt counts persist without a binding.
- Ordinary and binding email proofs cannot be substituted. Requesting another
  mailbox within a binding flow invalidates the old flow's email challenge.
  Email and binding sends share the existing mailbox/IP limits and safe delivery
  failures. No endpoint reveals whether a mailbox already owns a library.
- Browser flows last ten minutes. Starting another flow cancels the previous
  cookie-selected context; cancellation or a replay cannot complete it later.
  Start admission is bounded per HMAC-obscured requester; expired-row cleanup is
  bounded. A claimed callback commits `created -> exchanging` before provider
  HTTP, and remote code exchange occurs at most once outside DB transactions.
- Flow/CSRF cookies are `__Host-kb_external_flow` (HttpOnly) and
  `__Host-kb_external_csrf`, Secure, SameSite=Lax, Path=/ and no Domain.
  Pending mutations require exact Origin, cookie/header double-submit and the
  flow's stored CSRF hash. They do not borrow the normal Session CSRF cookie.
  Callback is the narrow protocol GET exception; use top-level query-mode
  navigation, not a cross-site form-post callback.
- Reject a new start when a valid Session already exists. Before callback or
  binding issues a Session, preserve any valid changed Session and return
  `session_changed`; an expired/stale cookie alone does not block login. Pending
  `completed` is compared with the exact completed session hash, not merely any
  logged-in account or equal method label. Lost cookies recover via a new
  provider login, never by replaying consumed proof to recover a raw token.
- Ordinary email verification forwards the HttpOnly external-flow cookie through
  the internal `external_login_flow_token` keyword when external auth is mounted.
  In the same successful email-login transaction, lock that exact flow before
  proof/owner rows, mark it `failed/session_changed`, and clear transient proof
  and completed-session hash. If it had completed, revoke its provenance-bearing
  Session by the recorded token hash. Wrong codes/rolled-back email attempts do
  not cancel the flow. A callback returning from provider HTTP must re-lock its
  `exchanging` row, so another tab's committed email login prevents the old
  callback from issuing or reviving an authenticated provider Session. Checking
  only the callback's stale request Cookie cannot establish this ordering.
- Transient SDK/PKCE material is AES-GCM encrypted using an HKDF purpose-specific
  key from `WEB_AUTH_SECRET`, with the flow digest as associated data. Store no
  access/refresh tokens after verified identity extraction. Raw tokens/codes,
  SDK material and provider responses never enter DTOs, ordinary logs or storage.
- Provider sessions execute one joined PostgreSQL authentication SELECT over
  session, enabled owner, exact email anchor and enabled external mapping. Check
  all owner/anchor/method/application equalities and current configured provider.
  They are not admitted to Redis; only the versioned email projection can have
  null external provenance. Preserve one request-scoped Session resolution and
  existing CSRF reuse. Legacy channel resolvers explicitly reject external
  provenance/methods instead of bypassing these checks.
- Explicit tenant merge moves mappings to the surviving owner while retaining
  their exact anchors, and revokes absorbed-owner sessions. Direct CLI rebind of
  an anchored external identity fails. Several already-linked email anchors may
  share an AppUser; do not add a one-email/one-provider-per-user assumption.
- The known standalone-CLI cross-process invalidation limitation for existing
  email Session cache is not repaired here. Provider security does not depend on
  that cache broadcast. Keep the residual limitation explicit in test evidence.
- Adapters use verified TLS and fixed hosts, finite timeouts, bounded responses,
  no arbitrary redirects, and opaque exceptions. `ProviderHTTP` avoids logging
  complete GET URLs because QQ/WeChat send credentials in query parameters.
  Application/proxy callback logging must also omit query strings.
- Microsoft uses actual MSAL confidential code flow with S256 PKCE/nonce and
  explicit RS256/JWKS signature, audience, expiry/time and tenant-issuer checks.
  Require the signing key's issuer to match the exact/token-templated issuer;
  missing key issuer fails. Never fetch untrusted token-header URLs. QQ verifies
  response AppID/OpenID; WeChat checks `snsapi_login` and token/userinfo OpenID
  agreement. Parse QQ JSON/form/JSONP without evaluation or duplicate fields.
- `WEB_MICROSOFT_ENABLED`, `WEB_WECHAT_WEBSITE_ENABLED`, `WEB_QQ_ENABLED` gate
  capabilities independently. Enabled providers need their private application
  credentials, Web email auth and secure cookies. Client/config changes invalidate
  unfinished flows. See `docs/reference/configuration.md` for exact variables.

## 4. Validation & Error Matrix

| Condition | Canonical result |
| --- | --- |
| Invalid Origin | 403 fixed `origin_forbidden`; no credential mutation. |
| Accepted-origin native start operational error | 303 to clean continuation, bounded browser-bound failure status. |
| Missing/foreign pending cookie | 200 `expired` status with `flow_invalid`, no new subject/proof. |
| Expired/used/foreign flow or invalid flow CSRF mutation | 403 `flow_expired`, `flow_used`, `flow_invalid`, or `flow_csrf_invalid`. |
| Wrong email code | 401 `verification_failed`, counted attempts; remain recoverable. |
| Invalid email | 422 `invalid_email`. |
| Existing/changed Session or binding ownership conflict | 409 `session_present`, `session_changed`, or `binding_conflict`; preserve the actual account. |
| Challenge send rate limit | Same 200 accepted response as a normal request. |
| Provider unavailable/failed or mail delivery unavailable | 503 bounded safe error; no provider text or credentials. |
| Denied/invalid callback | Leave the callback URL via 303; inspect safe pending status and restart. |
| Unexpected service/database failure | Fixed `request_failed` envelope or clean callback navigation; no internals. |

## 5. Good / Base / Bad Cases

- Good: first Microsoft authorization verifies the mailbox already owning a
  library, preserves its AppUser/anchor, and later Microsoft login reaches it
  without another email code.
- Base: all provider flags are false; normal email login and existing legacy
  embedding contracts remain usable. A stale Session cookie permits fresh login.
- Bad: email-claim auto-linking, provider-only registration, a session inserted
  before mapping commit, replaying an exchanged code, caching provider sessions,
  or deleting provenance into an ordinary email session.

## 6. Tests Required

- `tests/test_external_login.py` and `test_external_login_models.py`: no account
  before both proofs, exact proof purpose, changed mailbox, cancellation/replay,
  encrypted material, callback claim before HTTP, conflict/no orphan, failed
  transaction rollback, disabled-source checks, one joined SELECT, no provider
  cache write, and legacy resolver rejection.
- Route tests must interleave ordinary email verification inside provider code
  exchange, then assert the callback issues no Session. Also test completed-flow
  supersession, failed-email attempts and reordered responses: the superseded
  provider token is revoked, while the successful email Session remains valid.
- `tests/test_external_login_postgres.py`: isolated real PostgreSQL concurrent
  same-email/same-subject winners, different-owner rollback, email/provider
  registration race, callback claim, merge/rebind safety and additive migration
  roundtrip preserving email and old channel rows. SQLite alone is insufficient.
- `tests/test_external_login_routes.py` and provider/API runtime coverage: exact
  Origin/CSRF, clean 303s, duplicate/bounded callback parameters, safe public
  projections, capabilities and generated schema. Signed token tests must check
  wrong signature/audience/issuer/nonce/time, key issuer and key rotation; QQ and
  WeChat verify application/subject scope and credential-free logging.
- Preserve existing email/cache/logout/CSRF/tenant-isolation tests, single Alembic
  head, frontend gates and a fake-provider browser navigation smoke. Report
  registered-domain live-provider/mail delivery acceptance separately.

## 7. Wrong vs Correct

### Wrong

```python
email_auth.verify(email, code)  # commits a normal session first
mapping.app_user_id = provider_claims["email_owner"]
cache.put(digest, provider_session)
```

### Correct

```python
with factory() as db:
    proof = email_auth._verify_email_proof(db, email, code, current=now,
                                         external_login_flow_id=flow.id)
    if proof is None:
        db.commit()  # persist the bounded failed-attempt counter only
        raise InvalidVerification()
    tenant = email_auth._resolve_email_tenant(db, email)
    # Insert/validate the exact unique mapping under the same transaction.
    verified = email_auth._issue_session(db, tenant, current=now,
        login_channel=flow.provider, external_login_identity_id=mapping.id)
    proof.consumed_at = now
    db.commit()
# Return cookies only now. Provider sessions stay on the DB resolver path.
```
