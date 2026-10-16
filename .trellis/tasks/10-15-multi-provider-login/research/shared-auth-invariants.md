# Shared Authentication Data and Session Invariants

Research date: 2026-10-15. Planning only; no product implementation or task activation.

The confirmed experience is: authorize an external provider, verify an email on first use, explicitly bind to that email's existing or newly registered account, then use the bound provider directly on later visits. No separate provider-owned tenant is created before email verification. First release should omit user-facing unlinking, email replacement, provider-account replacement, and a multi-email management UI.

## Recommended Minimal Boundary

- Keep `AppUser.id` as the library owner and the exact verified `web/web/<canonical-email>` `ChannelIdentity` as the browser/conversation tenant. Existing email normalization remains authoritative; provider subjects must never pass through `canonical_email()`.
- Add `ExternalLoginIdentity` as a provider proof mapping, separate from messaging `ChannelIdentity`. Store a stable, case-sensitive provider identity key, owner, exact canonical email identity, creation time, and a soft-disable timestamp. Do not store provider access/refresh tokens for sign-in-only use.
- Add nullable `WebSession.external_login_identity_id` as immutable authentication provenance. A session completed through a provider, including its first email-verification completion, references that provider mapping. Email-only sessions leave it null.
- Provider-issued sessions use one authoritative database join on every request. They are never returned from or filled into Redis. The existing email-session cache remains the email-only path. This avoids making new provider disable/rebind safety depend on a broader distributed cache redesign.
- Preserve the existing browser cookie, CSRF, request-scoped session reuse, safe public DTO, library scope, and MCP/browser-companion transport boundaries.

## Mapping and Foreign-Key Shape

Suggested `ExternalLoginIdentity` fields and constraints:

| Field / constraint | Purpose |
| --- | --- |
| `id` primary key | Internal immutable provenance reference. |
| `provider`, `subject_namespace`, `subject` all non-null; unique together | One trusted external principal has one binding. Namespace is the adapter-validated issuer/application scope, not a browser-supplied provider URL or email. |
| `app_user_id` non-null FK to `app_user.id`, delete restricted | Avoid silently losing login mappings when a tenant-delete/merge path omits them. |
| `canonical_identity_id` non-null | The exact email identity verified during binding; never choose an arbitrary email by user ID. |
| Composite FK `(canonical_identity_id, app_user_id)` to `channel_identity(id, app_user_id)` | Enforce that the anchor and mapping have the same owner. Add the supporting parent unique constraint. Use a deferred `NO ACTION` FK if ownership is updated in stages during the existing atomic merge. |
| `disabled_at` nullable | Preserve a reserved, auditable identity key instead of deleting/reassigning it. |
| Indexes on owner and canonical identity | Bounded merge/rebind checks and revocation. |

The composite FK does not prove `channel='web'`, `account_id='web'`, canonical-email syntax, or enabled state; the shared service and authoritative resolver must still validate those conditions. No `ON UPDATE CASCADE` should silently transfer a login proof during the existing operator rebind command.

Do not add `UNIQUE(app_user_id, provider)` or assume one email identity per user. Even without a new multi-email UI, the existing Telegram/Web merge can put several already-existing email identities, and potentially several same-provider mappings, under the surviving user. Preserve their individually proved anchors.

For `WebSession.external_login_identity_id`, use an FK that restricts deletion, not `ON DELETE SET NULL`. A disabled/deleted mapping must never turn a provider session into a normal email session. First release can retain disabled mapping rows permanently. If hard deletion is introduced later, revoke or remove dependent sessions explicitly before deletion, and never reinterpret a removed provenance reference as email authentication.

Continue using the existing `WebSession.login_channel` (or an explicitly named method field if necessary) to distinguish the public login method. Preserve legacy rows: current email sessions store `login_channel='web'`, whereas HTTP DTOs translate this to `email`; migration-era channel sessions also exist. Add only compatible constraints/backfills. New provider records require a non-null provenance ID and a provider method matching the referenced mapping; null provenance is permitted only for supported email/legacy sources. Distinct internal provider keys must avoid confusing website WeChat with legacy messaging WeChat. Cross-owner or anchor-mismatched session rows must fail the resolver even if inserted outside the issuance service.

## Authoritative Session and Cache Rules

Current references: `app/web_auth.py:660` (`resolve_session`), `:383` (`RedisSessionCache._project`), `:495` (`put`), `:743` (`revoke_session`), `:754` (`revoke_user_sessions`), `:804` (transaction invalidation hooks); `.trellis/spec/backend/web-browser-runtime.md`.

For a provider session, the single joined query must enforce all of:

1. Existing token hash, unrevoked session, and future expiry.
2. Enabled `AppUser` and enabled canonical `ChannelIdentity` in the exact `web/web` namespace.
3. `session.app_user_id == canonical_identity.app_user_id`.
4. Non-null external provenance resolving to an enabled `ExternalLoginIdentity`.
5. Mapping owner equals session owner, and mapping anchor equals the session's exact `channel_identity_id`.
6. Stored method matches the mapping's provider; the provider is enabled and fully configured by the current server policy.

An outer join can handle nullable email provenance while retaining one authoritative statement. Do not use an inner join that accidentally rejects all old email sessions. Capture the immutable resolved projection before commit/rollback, and keep the existing throttled `last_used_at` behavior and one-resolution-per-request CSRF boundary.

The shared `AuthenticatedWebSession` projection needs enough provenance/method information for truthful DTOs and cache admission. A versioned Redis email projection must explicitly represent a null external provenance ID and email source. Missing, provider-sourced, method-mismatched, or malformed projections must go to PostgreSQL; `put()` must refuse provider sessions. Change the payload version/prefix when the shape changes so old payloads cannot silently omit provenance. Never retag an existing session token from provider to email.

This preserves warm email-cache performance while provider requests deliberately pay one bounded authentication query. An enabled-state change committed before the next provider request is observed by that request without depending on Redis broadcasting. A request already authorized before a mutation commits may finish; this does not promise cancellation of in-flight requests.

Disabling a provider globally must prevent both new login and use of its existing provider-issued sessions. Disabling a single mapping also revokes sessions with that provenance ID in the same transaction. Keep the disabled mapping and immutable provenance so later re-enabling cannot revive those revoked sessions. Existing email-only sessions remain independently usable unless the user/anchor itself is disabled.

## Atomic First Login and Binding

`app/web_auth.py:600` currently combines email proof consumption, implicit registration, and session creation. Extract transaction-scoped helpers rather than calling the committing `verify()` and then binding in another transaction. A normal email login and provider completion must share the same canonical-email get-or-create operation.

Required transaction result: either the valid email challenge and pending provider proof are consumed, the mapping is created/resolved under the correct existing owner, and one new provider-provenance session is persisted; or none of those ownership/session/consumption changes commit. Set browser cookies only after successful commit. Failed code attempts may still increment/commit their bounded attempt counter, but must not consume/bind the pending provider proof or create an authenticated session.

Suggested sequence:

1. Lock and validate the one-time browser-bound pending provider proof and intended email challenge; confirm expiry, binding intent, provider policy, and verification code. Proof values remain server-side/hash-protected as appropriate.
2. Resolve/create the canonical email identity without changing an existing identity's owner. A `SELECT ... FOR UPDATE` that finds no row does not serialize competing registrations. Let the unique `(channel, account_id, external_user_id)` constraint choose one committed winner; use a properly placed SAVEPOINT or a carefully structured insert-conflict path to discard a losing candidate `AppUser` and reload the winner.
3. Lock/revalidate the relevant owner, canonical identity, and external mapping using the same owner-first order as merge/rebind operations. If an earlier unlocked lookup was used to find the owner, re-read after acquiring the lock and fail/retry on ownership change. New issuance and disable/merge operations must not pass each other without one seeing the other's result.
4. Insert the external mapping behind its unique key using a recoverable SAVEPOINT. If another transaction wins, re-read it: same owner **and exact anchor** is idempotent; different owner, different anchor, or disabled mapping is a bounded conflict, never reassignment. Roll back a newly created candidate user/identity on conflict.
5. Consume both proofs, persist a session with the mapping provenance, capture its immutable projection, and commit once. Two distinct pending proofs for the same external identity and email can resolve the same binding; replay of one consumed pending proof still fails.

Avoid nested-service commits, token exposure before commit, and swallowing `IntegrityError` while leaving SQLAlchemy's outer transaction failed. Keep conflict/deadlock retries bounded and repeat only transaction-local work; do not repeat remote authorization-code exchange or send email inside a database retry loop.

### Existing SAVEPOINT Trap Confirmed Locally

`app/channels/identity.py:144-171` adds `ChannelIdentity` **before** `db.begin_nested()`. SQLAlchemy pre-flushes pending state when entering that context. A unique failure can therefore happen before a SAVEPOINT exists; its handler cannot safely query the winner without rolling back the failed outer transaction.

A credential-free, in-memory SQLite probe using the repository `.venv` confirmed the SQLAlchemy behavior: add-before-nested conflict leaves `Session.is_active=False`; add-inside-nested leaves it `True`. This probe did not touch application databases or files. It verifies SAVEPOINT placement, not PostgreSQL concurrency semantics.

For the new shared registration helper, enter the SAVEPOINT **before adding/flushing the candidate user and identity**. Ensure any unrelated pending ORM state has been deliberately flushed before opening the savepoint. Verify the true registration/binding races against isolated PostgreSQL; SQLite does not model PostgreSQL row locking or concurrent unique-conflict waits.

## Existing Mutation and Deletion Audit

The following is the complete set of relevant first-party mutation entry points found under `app/` and `scripts/` by searching user/identity/session deletes, ownership updates, disabled timestamps, revocation helpers, and cache invalidation. Direct SQL outside these services remains an operator responsibility; no ORM hook currently makes arbitrary writes safe.

| Existing path | Current behavior | Required provider-specific handling / limit |
| --- | --- | --- |
| `app/web_auth.py:600`, email verification | Finds or creates `web/web/email`; issues a session; no cross-tenant merge. | Share transaction-scoped proof/registration/session helpers with provider completion. Email-only sessions have null provenance. |
| `app/web_auth.py:743`, email logout | Sets `WebSession.revoked_at`, commits, then invalidates this service's cache. | Same revoke operation works for provider sessions because every provider request checks PostgreSQL. Keep session cookies cleared by canonical HTTP router. |
| `app/web_auth.py:754`, user-session revocation | Revokes all sessions for a user and marks commit-time cache invalidation. | Includes provider sessions automatically; no method filter may omit them. |
| `app/cli.py:177`, user disable | Sets user disabled, calls `revoke_web_sessions`, commits. | DB-only provider resolution rejects the user immediately after commit; keep revocation so re-enabling the user does not revive old provider sessions. |
| `app/cli.py:181`, user enable | Clears user disabled timestamp. | Does not clear session revocation or mapping disable timestamps. |
| `app/cli.py:163`, `rebind-identity` | Directly changes `ChannelIdentity.app_user_id`; no session revoke or cache-invalidation marker. | Do not silently transfer a provider login with its anchor. Minimum first-release policy: reject a direct rebind of any canonical identity referenced by an external mapping. A future explicit rebind service must disable affected mappings and revoke provider sessions, or perform a fully reviewed ownership migration, in one transaction. |
| `app/channels/identity.py:243-314`, link-token consumption | Locks token/users, proves both channel contexts, merges target into token creator, consumes token atomically. | Keep login/binding out of this path. Extend its existing merge for mappings already present on either user. |
| `app/channels/identity.py:317-473`, `_merge_tenants` | Locks identities/threads/tokens/sessions/MCP grants/content; reconciles content/dispatches; moves target identities/threads/tokens; moves and revokes target sessions/MCP grants; deletes target root. | Lock external mappings in deterministic order. Preserve each exact canonical anchor; move target mapping owners to the survivor after channel identities move. Keep/revoke target provider sessions with existing logic; explicitly revoke any session whose provenance would become inconsistent. A disabled mapping stays disabled. Complete all updates before root deletion. Restrictive/deferred FKs make an omitted mapping migration fail atomically rather than silently delete a login method. |
| `app/channels/service.py:539-566` and `app/api/conversation_routes.py:1531-1601` | Bot and browser entry points call the same link-token functions. Browser consumption clears cookies; frontend clears private caches and returns to login. | Regression-test mapping migration through both directions without changing the existing Telegram linking UX. Provider login must work under the surviving owner after the merge; target pre-merge provider sessions must remain revoked. |
| `app/channels/identity.py:180-211`, channel self-registration | Creates temporary user; loses unique identity race via `ON CONFLICT`, then deletes that temporary user. | No provider mapping may be attached to this losing candidate before the winner is known. Provider registration is the email-anchored shared flow, not this messaging bootstrap. |
| `app/web/auth.py:234-249`, legacy retention cleanup | Deletes only sufficiently old expired/revoked `WebSession` rows. | Session-to-mapping FK direction must permit deleting sessions without deleting a mapping. No cache/session validity extension past actual expiry. |
| `app/web/auth.py:405-454`, legacy resolver/logout | Checks legacy session/user; has no external mapping checks or shared cache invalidation. | Production provider sessions must resolve only through the canonical shared resolver. Legacy resolver must reject provider provenance if it can encounter the new rows; do not turn it into a provider-auth fallback. |

No user-facing account-delete, canonical-email-disable, external-mapping-delete, or external-provider-unlink implementation currently exists. `AppUser` and `ChannelIdentity` disabled timestamps can also be changed directly in tests/operator SQL; new supported mapping-disable operations need explicit row/session mutation, not an assumption that a generic ORM hook revokes credentials.

### Existing Merge Dependencies That Must Be Recognized

`ChannelIdentity`, `ChannelLinkToken`, `WebSession`, `ConversationThread`, `McpAccessGrant`, `BrowserCompanionPairing`, `BrowserCompanionGrant`, `BrowserCapture`, and approved legacy login challenges reference `AppUser` with cascading deletion. `ContentItem.user_id` uses an ordinary owner FK. The current merge explicitly moves/revokes the first group of owned identities/conversations/tokens/sessions/MCP credentials and reconciles content/dispatches, but does **not** explicitly migrate browser-companion pairings/grants or browser-capture records before deleting the absorbed user. Their cascade behavior is pre-existing and is not repaired by this SSO task.

The new mapping must not copy those accidental cascade semantics. Restrict its deletion and explicitly move it during the already-authorized channel merge. Keep new SSO acceptance limited to preserving libraries/history and mappings; do not claim a general repair of every older account-merge capability. If provider development changes browser-companion behavior, expand scope and tests deliberately rather than treating the issue as solved by moving an identity.

## Existing Redis Invalidation Boundary

`mark_web_session_cache_invalidation()` records a flag in `Session.info`; outermost `after_commit` calls `invalidate_web_session_cache()`, which iterates the current process's `WebAuthService._cache_instances` WeakSet. The generation key is shared in Redis, but discovering which cache object should bump it is process-local. The standalone CLI does not construct an email `WebAuthService` before its disable/rebind operations. Consequently, a CLI commit with no registered cache instance does not bump another Web process's Redis generation. A direct identity rebind does not even mark invalidation. These are real existing limits, not guarantees supplied by the hooks.

Likewise, a failed Redis invalidation sets `_generation_bump_required` on the affected cache object; it is not a durable failure marker shared with every other process. Existing tests cover same-process generation races, nested-transaction timing, and recovery, but do not prove arbitrary cross-process mutation/recovery coherence.

Do not claim this task fixes all existing email-cache invalidation paths. The deliberately uncached provider-session path closes this new feature's admission boundary without adding an event bus, database epoch read per warm email request, or distributed invalidation protocol. Provider Session tests must demonstrate security even when generation bumping is absent/fails and another process has an email cache. Any future decision to cache provider sessions requires a separately reviewed cross-process invalidation design and tests.

## Focused Verification for Shared Foundations

- Isolated PostgreSQL: simultaneous same-email registration/proof completion, same provider+email binding, same provider with different emails, losing candidate cleanup, no orphan `AppUser`, one mapping per external key, and bounded conflict rollback.
- Inject failure after code validation, candidate creation, mapping insert, and before commit: no partly consumed proofs, partial mapping, or authenticated session; wrong-code attempts retain their intended bounded-counter behavior.
- DB constraints and resolver: foreign owner, wrong exact anchor, disabled user/anchor/mapping, missing mapping, null/mismatched provenance, wrong provider method, malformed canonical namespace, and disabled global provider policy all fail closed.
- Provider sessions always execute one joined authentication SELECT and are never cached. Email warm hits retain their existing zero-SELECT assertion. A forged/old/provider cache projection cannot bypass DB resolution; CSRF/logout continue resolving once per request.
- Disable mapping and revoke provider sessions while Redis is healthy/down, with no local cache instance, and across independent service instances; next provider request fails. Re-enable mapping/user does not revive revoked sessions. Email-only login remains an independent fallback.
- Existing link-token merge in both directions: each mapping and exact email anchor survives under the surviving owner; old target provider sessions fail; new provider login reaches original content/history; disabled mappings stay disabled. Direct operator rebind of an anchored identity is rejected without partial ownership changes.
- Compatibility migration: old email rows and legacy channel rows still work through their intended resolver; one Alembic head; schema/OpenAPI/public method changes stay coordinated; no provider proof or private identifiers enter browser storage/logs/DTOs.

These checks extend `tests/test_web_email_auth.py`, `tests/test_web_session_cache.py`, `tests/test_multiuser_integration.py`, auth model/migration coverage, and CLI tests. The planning work ran only the in-memory SAVEPOINT probe above; it did not run product tests, use provider credentials, or contact a database.
