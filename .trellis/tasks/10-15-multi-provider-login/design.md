# Multi-provider Login Design

## Decision and Boundaries

The user selected email-anchored first use. An external identity is a new way to enter an existing email-owned StashSeek account. First use proves control of the provider and a mailbox, explicitly binds them, then issues a session. Later use proves the same provider subject and enters the same account directly.

Keep `AppUser.id` as the library owner and `web/web/<canonical-email>` as the browser tenant identity. No social-only tenant is created, no provider email claim selects an owner, and normal provider sign-in never calls the tenant-merge function. Existing Telegram linking remains a distinct, explicit ownership operation.

The canonical FastAPI composition owns all routes and cookies. New modules may isolate external-provider flow and adapters, but cannot introduce another session authority, cookie parser, private-cache owner, or frontend identity store. The foundation child implements shared behavior against a test-only provider; production providers remain unavailable until their adapter and configuration are valid.

## Shared Data Model

Add additive Alembic schema changes; names below define intended model responsibilities, with final column lengths matching bounded provider responses:

1. `ExternalLoginIdentity`: provider kind, application/client scope, canonical issuer/namespace, stable subject, `app_user_id`, **exact** canonical email `channel_identity_id`, timestamps, and disabled state. All parts of the unique provider/application/namespace/subject key are non-null and non-empty: Microsoft stores its validated issuer, QQ/WeChat store fixed server-defined provider namespaces with the registered application scope. Do not use nullable issuer columns that let PostgreSQL uniqueness admit duplicate NULL keys. Both owner and anchor must agree when issuing/resolving sessions. Add a supporting unique `(ChannelIdentity.id, app_user_id)` key and a deferred composite mapping-to-anchor owner FK; root deletion is restricted until explicit mapping migration. Microsoft uses app-scoped `sub`; WeChat/QQ use app-scoped OpenID. Subject values remain case-sensitive. UnionID/name/email are not automatic merge keys.
2. `ExternalLoginFlow`: hashed random browser-flow token and state, provider/application/configuration scope, phase, expiry, protected transient provider-flow material, verified subject only after callback validation, and any purpose-bound email-challenge association. A flow expires after 10 minutes; extending it requires a new provider authorization. One active flow per browser is sufficient for the first release; starting another invalidates the old browser context.
3. `WebAuthChallenge`: distinguish ordinary email login from a specific external-binding flow. Keep existing rows compatible as ordinary email login. Shared helpers retain rate limits across both purposes, hashed codes, attempt limits, resend invalidation, and delivery failure behavior. A code requested for ordinary login or another flow cannot finish binding.
4. `WebSession`: nullable `external_login_identity_id` provenance plus the login method needed by the public session projection. Preserve existing `login_channel='web'` email rows and legacy Telegram/WeChat channel rows, including their nullable identity linkage, under their respective resolvers; never backfill all historical sessions as email. Provider provenance uses a restrictive FK with retained soft-disabled mapping rows; never `ON DELETE SET NULL` or reinterpret removed provenance as email authentication. Validate provider methods against non-null provenance and supported email/legacy methods against their compatible null-provenance shapes.

An existing tenant may already have several email identities after supported linking. Map to the precise verified email anchor; never select an arbitrary first email for a user.

Persist session/CSRF/flow lookup values only as hashes. If an SDK requires a PKCE verifier or full authorization-flow dictionary across requests, store that short-lived material encrypted on the server with a maintained authenticated-encryption library and a purpose-separated key derived from the configured high-entropy auth secret. Do not store access/refresh tokens after identity verification; no Graph/mailbox API scope or refresh capability is required.

## Browser/API Contract

Use same-origin top-level navigation for authorization, avoiding popup/iframe cookie dependencies:

| Endpoint | Contract |
| --- | --- |
| `POST /api/v1/auth/external/{provider}/start` | A native form/navigation with exact Origin. Resolve a configured provider, create the browser-bound flow, set a host-only HttpOnly flow cookie, and return a 303 to the fixed provider authorization endpoint. No user-supplied redirect URI, tenant, subject, or arbitrary return URL. |
| `GET /api/v1/auth/external/{provider}/callback` | Protocol callback only: bounded, non-duplicated code/state/error parameters; validate provider/config/flow/cookie/expiry and atomically claim the callback before exchange. It is the narrow OAuth exception to the normal no-state-changing-GET rule. Always leave the credential-bearing URL through a 303 to a fixed clean page. |
| `GET /api/v1/auth/external/pending` | Browser flow cookie selects the flow. Return only a closed status enum, provider name and expiry; no identity IDs, email address, authorization code, state, SDK payload, or tokens. |
| `POST /api/v1/auth/external/pending/challenges` | Exact Origin and flow-bound CSRF, strict `{email}` body. Request a code scoped to this validated provider-binding flow. Preserve accepted/rate-limit indistinguishability. |
| `POST /api/v1/auth/external/pending/verify` | Exact Origin and flow-bound CSRF; strict `{email, code, confirm_binding:true}`. Verify the exact purpose-bound challenge, resolve/create the email account, bind the provider, and issue a browser session in one database transaction. |
| `DELETE /api/v1/auth/external/pending` | Exact Origin and flow-bound CSRF; cancel the flow and expire its cookies. No ownership change. |

For accepted-origin native start requests, operational failures such as an existing valid Session, disabled provider or configuration change also return a 303 to the clean application continuation page with a bounded browser-bound server status. The user must not remain on a JSON error document. Invalid-Origin/protocol abuse retains the canonical fixed rejection response without provider detail or session changes.

Provider values are the closed enum `microsoft`, `wechat_website`, `qq`. Keep legacy `wechat` messaging login distinct. Extend public capabilities with `external_login_providers`; preserve `web_login_channels` for current email/legacy compatibility. The existing `SessionResponse.login_channel` can add these three method values while keeping its identifier-free shape. Export OpenAPI/TypeScript from the real canonical composition.

The flow cookie uses a random opaque value, `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, and no Domain in production (a `__Host-` name). Its companion CSRF value is separately random, stored hashed on the flow and double-submitted on pending-flow mutations. Extend the canonical JSON wrapper with an explicit internal flow-CSRF request context selecting this cookie; it must not blindly send the ordinary session-CSRF cookie for pending requests. A top-level GET callback carries the Lax cookie; do not choose a cross-site form-post callback mode. Development exceptions must follow the existing explicit loopback cookie configuration and never relax production settings.

Start is permitted only in an unauthenticated browser. If a valid private session exists, retain it and show safe guidance to sign out before using a different login flow; do not silently replace an account or bind based on whichever cookie happens to be present. Before callback session issuance and first-bind verification, recheck that another tab has not introduced/changed an authenticated session; on `session_changed`, preserve the actual session and require an intentional restart. A stale cookie alone is not a valid private session and must not block a fresh login. The pending-flow record and callback claim also bind the intended operation, not merely the callback's provider name.

## Flow and Transaction Rules

1. Start generates server-side state and browser proof, records configured provider scope and allowed callback, then navigates to the provider. Bound start attempts by hashed client IP/browser and response sizes; unfinished flows cannot grow without an expiry/retention bound.
2. Callback checks browser binding and atomically moves `created -> exchanging`, committing the claim before external HTTP. Duplicate/replayed callbacks cannot exchange twice. Never hold a database transaction open during provider HTTP.
3. The adapter validates the response and returns only a trusted stable identity. Network, malformed, provider-denied, wrong-state, disabled-provider, and validation failures end the attempt; restarting is safe and required after a consumed code/network ambiguity.
4. A bound enabled identity with a valid matching owner/anchor issues a provider-provenance browser session and redirects to `/library`.
5. An unbound subject moves to `email_required` and redirects to `/login/external`. No tenant or session is created yet. The page explains which mailbox determines the library, permits code request/resend, and labels the final action as confirmation to bind.
6. Verification locks the flow/challenge, persists failed-code attempt counts, and on successful proof atomically resolves the canonical email identity, inserts or confirms the unique external mapping, consumes the flow/challenge, and creates the session. Capture safe session values before commit and set cookies only after a successful commit.
7. For missing-email races, build candidate rows inside the savepoint, or use a bounded database conflict strategy and re-read the winner. Do not add a candidate before entering `begin_nested()` and assume the outer transaction survives an automatic flush conflict. For an external-key conflict, re-read under lock and require exact ownership/anchor; otherwise return a bounded conflict with no session or partial binding.

Expired/cancelled/failed flows show a fixed restart message. Terminal callback errors may be represented by a short-lived safe server-side status tied to that browser, never by echoing provider text. Missing/foreign cookies receive generic guidance without creating a flow for a subject supplied by the request. Set `Cache-Control: no-store` and `Referrer-Policy: no-referrer` on sensitive routes/pages; redact callback query strings at application/proxy logging boundaries.

After an uncertain final-submit network result, re-read the authoritative session and safe pending status before retrying. A completed binding with no delivered browser session can restart provider sign-in and use the already-bound identity; never reassign ownership or replay a consumed proof to recover a raw token. Keep email/code drafts only in component memory; refresh may require re-entering the mailbox/code, while the server remains authoritative about which challenge belongs to the flow.

The anonymous login coordinator needs a login-only session-probe wrapper in the canonical API client. Its expected `401 session_invalid` is handled locally, clearing stale private data without navigating away from `/login/external` or consuming the pending flow. Protected-route requests keep the existing global invalid-session handler. Scope the exception to this probe; stale/aborted probe responses cannot clear or replace a newer accepted session. No second session endpoint or cookie authority is introduced.

## Sessions, Caches and Existing Ownership Operations

Extract transaction-local email-proof, email-account resolution, and session-issuance helpers from the existing service. Both ordinary email login and external binding use them. Do not call today's committing `verify()` and append a provider mapping afterward.

Provider-issued sessions use one bounded database join to validate session, enabled owner, exact email anchor, external identity provenance, matching ownership, and currently enabled provider configuration. **Do not cache provider-session projections in Redis in this first release.** Existing canonical-email session caching remains unchanged. Cached email payloads cannot represent or downgrade external provenance, and the store path explicitly rejects provider sessions. This preserves the current browser tenant contract and avoids making new provider revocation depend on cross-process cache broadcasts.

Legacy channel-only session resolvers must explicitly reject provider provenance/methods if they can encounter the new rows; they cannot serve as a fallback that omits provider validation. Ordinary historical channel sessions retain their established behavior.

Audit these ownership operations as part of the foundation:

- Existing explicit tenant merge moves external mappings to the surviving owner while retaining the exact email anchor, and revokes provider sessions belonging to the absorbed owner before deletion. No mapping is silently lost through a cascading root deletion.
- Direct CLI rebind of an email anchor that has external mappings must fail with safe guidance rather than transferring social login access to a different tenant. A separate administrative rebind workflow is out of scope.
- Disabled owner/anchor/provider identity/configuration fails provider-session resolution even for previously valid sessions.
- New identity/session cleanup and cascade behavior cannot remove provenance while leaving an active session.
- Existing session-invalid/logout transitions still clear and replace the frontend private QueryClient before another account is shown.

Research found a pre-existing cross-process invalidation limitation for email-session cache in standalone CLI paths. This feature does not claim to repair all legacy cache invalidation. Record it for separate remediation, preserve existing email tests, and prove that new provider sessions bypass that limitation through database validation.

## Provider Adapters

The adapter boundary accepts only configured fixed endpoints and returns a verified stable identity plus public provider kind. All outbound HTTP uses verified TLS, timeouts, bounded responses, no automatic arbitrary redirect following, and redacted errors. Provider/client scope is rechecked at callback and binding; changing credentials/client ID invalidates unfinished flows rather than linking under another application.

- **Microsoft:** personal and organizational account registration; authorization code + S256 PKCE + OIDC nonce. Use MSAL Python's documented confidential-client code-flow helpers, keep SDK flow server-side, and use a maintained JOSE verifier for explicit signature/algorithm/JWKS and claim checks where the SDK contract does not provide them. Validate audience, nonce, timestamps, and exact tenant issuer according to Microsoft multi-tenant metadata, not the literal `common` URL. Fix discovery/JWKS authorities in configuration; never fetch a URL from an untrusted token header. Use validated issuer + application-scoped subject. Keep refresh tokens and Graph permissions out of the flow.
- **QQ:** documented server-side code flow, strict browser-bound state, exact redirect match, token exchange and app-scoped OpenID verification. Confirm the OpenID response's client/application matches this configuration. Parse only documented JSON/form/JSONP envelopes without evaluation, enforce the schema, and do not treat OpenID as an email or union key.
- **WeChat website:** verified official website authorization uses `https://open.weixin.qq.com/connect/qrconnect` with `snsapi_login`, server GET code exchange at `https://api.weixin.qq.com/sns/oauth2/access_token` and identity at `https://api.weixin.qq.com/sns/userinfo`. Bind to AppID/OpenID and require token/userinfo subject consistency. Use mandatory local browser-bound state even though the provider table calls it optional. The documented AppSecret flow is the initial supported mode; no official evidence makes separate API signing/encryption or PKCE/OIDC mandatory for it. If a registered application's console requires another mode, reject unsupported configuration and review that mode before enabling it. Official rejection does not redirect, so expiry/restart must work without a callback. Application/permission approval and personal eligibility remain live-registration prerequisites, distinct from the now-verified protocol evidence.

Dependency versions are selected and pinned from current supported primary documentation during the relevant child implementation; no package installation or lockfile change occurs during planning.

## Compatibility, Rollout and Rollback

The migration is additive and has one Alembic head; existing user, email identity, library, and session IDs remain valid. Deploy schema before enabling new code using the established one-shot migration admission. Runtime configuration has independent provider enable flags, IDs and private credentials; an enabled but incomplete configuration is rejected at startup, and an unconfigured provider is absent from capabilities.

Registration guidance uses the exact production HTTPS origin plus the above callback paths. Domain/application approval, mail delivery, and credentials are prerequisites for a real smoke. Fake-provider tests do not satisfy them, and the local demo API remains visibly separate from production auth.

Roll back by disabling provider entry points and revoking provider-issued sessions; leave additive tables/data intact. Return to the previous application release only after provider sessions are revoked, so older code cannot treat provenance-bearing sessions as ordinary email sessions. Do not downgrade/delete production auth tables as an automatic rollback. No deployment or production migration is authorized by approval of this implementation plan.
