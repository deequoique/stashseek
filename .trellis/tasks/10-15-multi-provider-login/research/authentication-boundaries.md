# Existing Authentication Boundaries

Read-only investigation on 2026-10-15. This research does not authorize implementation.

## Ownership and Existing Entry Points

- `app/models.py:64`: `AppUser.id` is the internal tenant root. `ChannelIdentity` binds a unique `(channel, account_id, external_user_id)` to one tenant. Libraries must retain this owner.
- `app/web_auth.py:600`: `WebAuthService.verify()` verifies an email challenge, creates or resolves the `web/web/<canonical-email>` identity, then issues a hash-only browser session and CSRF token. A verified email creates an account if none exists.
- `app/web_auth.py:660`: `resolve_session()` validates session expiry/revocation, enabled user and identity, matching ownership, and the `web/web` namespace in one database query.
- `app/web_auth.py:383`: the Redis session projection additionally accepts only canonical-email external IDs. A generic provider callback cannot simply insert arbitrary provider IDs into this contract.
- `app/models.py:253`: `WebSession` ties session tokens to an `AppUser` and optionally a `ChannelIdentity`. Existing sessions and email identities must remain compatible.
- `app/api/email_auth_routes.py:132`, `app/api/app.py`, `app/api/runtime.py`, `app/web_runtime.py`: the canonical browser app owns routes, cookies, session resolution, and production composition. No parallel auth middleware or independent cookie/session parser should be introduced.
- `app/api/auth_schemas.py:40`, capabilities in `app/api/app.py:123`, and generated `web/src/api/openapi.json` / `schema.d.ts` currently enumerate existing login methods. New public contracts must be designed and exported together.
- `web/src/auth/LoginPage.tsx`: existing email/code states can support first-use email proof; the legacy channel instructions are a different authentication path.

## Identity Separation and Linking

Current WeChat identities describe trusted messaging-channel envelopes. Website WeChat OpenIDs, QQ OpenIDs, and Microsoft subjects are application/provider-scoped and must never be matched to Bot IDs, display names, or unchecked email claims.

`app/channels/identity.py:103` (`ensure_explicit_identity`) rejects a cross-tenant identity reassignment. Existing link-token code can merge tenants, including owned records and credentials. That merge is not an ordinary sign-in operation and must not be triggered just because provider emails match.

The canonical account-linking specs are:

- `.trellis/spec/backend/channel-identity-linking.md`
- `.trellis/spec/frontend/account-linking.md`
- `.trellis/spec/backend/web-browser-runtime.md`

## Product Decision Changes the Architecture

If all new provider users first verify an email, the provider can map to the existing email-owned `AppUser`; the canonical email browser identity/session projection can stay intact. Store the verified provider subject in a separate, unique mapping and issue sessions through one shared service. Pending provider proof must be short-lived, browser-bound, server-side, and cannot grant access before email proof and explicit binding.

If provider-only registration is allowed, session identity projection, cache validation, conversation adapters, and account-linking/merge semantics need broader changes. A fresh social account must never be silently merged into an email account. This option requires acceptance for two already-populated accounts and credential/session invalidation across a merge.

These are design alternatives, not an approved implementation.

## Required Design/Verification Coverage

- Stable provider identity keys, unique constraints, first-login concurrency, and conflict handling.
- One-time browser-bound state; Microsoft PKCE/nonce plus signature, issuer, audience, and expiry validation; provider tokens stay server-side.
- Disabled accounts/identities, revocation, session-cache invalidation after outer commit, and no stale private frontend cache on account change.
- Continue `__Host-` cookie, exact-Origin/double-submit CSRF, safe errors, and credential isolation from MCP/browser companion.
- Never log codes, raw state, provider tokens, client secrets, full identity mappings, or callback query strings containing authorization credentials.
- Tests should extend `tests/test_web_email_auth.py`, `tests/test_web_session_cache.py`, multiuser integration tests, frontend login/API tests, and real production-composition/OpenAPI checks.

## Existing Worktree Boundary

The checkout contains earlier uncommitted auth, history-performance, chat keyboard/scroll/layout, API-schema, and specification changes. This login task must preserve them and must not claim or commit them as newly implemented SSO work.
