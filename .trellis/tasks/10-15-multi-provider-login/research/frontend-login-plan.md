# Frontend Login State and Contract Plan

Date: 2026-10-15. Research only: no product UI, source changes, task start, dependency installation, or deployment is authorized by this document. The parent `design.md` is canonical; this research describes the frontend consequences of that design.

The chosen experience is email-anchored first use. Microsoft, WeChat website login, and QQ first establish provider control, then the user enters an email address and code and explicitly submits **Verify and bind** (`验证并绑定`). The server verifies the code, binds the provider to that exact email-owned account, and creates its Session in one transaction. There is no separate persistent confirmation phase or additional confirmation click. Subsequent sign-in through the bound provider opens the same library directly. Different mailbox services use the existing common email-code flow, without mailbox-vendor OAuth integrations.

## Existing Frontend Boundaries

- `web/src/auth/LoginPage.tsx` already owns in-memory email/code drafts, email/code form steps, pending mutations, recoverable errors, and an `onAuthenticated` callback. First-use external binding can share presentation components, but must use the purpose-bound external verification endpoint and explicit binding label rather than ordinary email-login verification.
- The current `emailEnabled ? EmailLoginFlow : legacyChallenge/methods` rendering is exclusive: enabling email hides the legacy method list. The new provider options must remain reachable alongside email, rather than simply adding three buttons to the hidden branch.
- `web/src/app/App.tsx` routes successful email authentication through `LoginRoute`: clear the old QueryClient, activate a fresh private client containing the authoritative Session and optional public capabilities snapshot, and replace-navigate. Reuse this ownership boundary for the external verification result. `ProtectedLayout` remains responsible for Session checks and nonblocking history preload.
- `requestJson()` owns JSON requests, same-origin credentials, and CSRF headers. Only `401 + session_invalid` invokes the global private-cache teardown. Recoverable code, flow, cancellation, and binding errors must stay local.
- `web_login_channels` and `SessionInfo.login_channel` currently enumerate `email`, `telegram`, and `wechat`. `LegacyLoginChannel` is derived from the chat-command challenge contract; legacy `wechat` is not website OAuth.
- `AppShell` currently labels Telegram, then email, then all remaining values as WeChat. Expanding `SessionResponse.login_channel` requires an explicit mapping for the three new methods, rather than allowing Microsoft or QQ to fall through to WeChat.

Relevant frontend specifications are `state-management.md`, `hook-guidelines.md`, `quality-guidelines.md`, `type-safety.md`, and `account-linking.md`. Existing Telegram linking and tenant merge remain separate explicit ownership operations; their tokens and router notices cannot carry external-login transactions.

## State Ownership and Clean Navigation

The backend owns the short-lived browser-bound flow through an HttpOnly Cookie and server-side records. Provider state, authorization codes, PKCE verifiers, nonce values, SDK payloads, stable subjects, and tokens do not enter frontend DTOs, React Router state, SPA URLs/fragments, Storage, persistent QueryCache, logs, or analytics. The frontend retains only provider name, safe phase/expiry, in-memory email/code drafts, and a local interaction generation used to discard stale UI work. That generation is not OAuth state and conveys no identity authority.

Start uses a native same-origin form POST to `/api/v1/auth/external/{provider}/start`, where the provider comes from the generated closed enum. The server checks exact Origin, creates the flow and Cookie, and returns a 303 directly to its configured provider endpoint. The browser performs normal top-level navigation. There is no frontend JSON prepare call, intermediate GET redirect endpoint, returned authorization-URL DTO, popup, iframe, or credential-bearing `postMessage` bridge. Do not invoke start through `fetch` and attempt to follow a cross-origin 303 in JavaScript.

Provider callbacks belong to the backend, including their necessary protocol parameters. The backend validates/consumes those parameters and leaves the callback URL by 303. An unbound subject or safe terminal result reaches clean `/login/external`; a successfully bound identity reaches `/library` as specified by the parent design. The SPA does not parse callback parameters, exchange tokens, or copy them into history. Application-side replace navigation cleans the app continuation entry; it cannot erase the provider's own browser history.

## Frontend State Machine

These are UI states, not a second handwritten API enum. Server phases and safe error codes must come from the generated contract. In particular, a locally displayed code-entry step does not require an additional durable server confirmation phase.

| UI state | Entry and action | Protected library access |
| --- | --- | --- |
| Checking | Read capabilities, the authoritative Session where relevant, and the current pending-flow status; distinguish pending, failure, and absence | Only after a valid Session is confirmed |
| Method selection | Offer email plus configured external providers; preserve any explicitly enabled legacy methods | No additional access |
| Starting | Submit the native provider-start form once and allow top-level navigation | No |
| External authorization | The browser is on the provider's site; returning/reopening does not prove completion | No |
| Resolving clean return | `/login/external` reads backend pending status rather than URL/referrer/original component memory | No |
| Email required | Valid unbound provider proof exists; clearly explain that the entered mailbox determines the library and request its code | No |
| Code entry | The code request was accepted; show the exact locally entered email, binding explanation, code input, resend/change-email/cancel actions, and **Verify and bind** | No |
| Verifying and binding | Submit `{email, code, confirm_binding:true}` to the pending verification endpoint; disable duplicate submit | No until the transaction succeeds |
| Authenticated | The server completed binding/session issuance, or a previously bound provider authenticated successfully; activate through the shared Session/cache boundary | Yes |
| Cancelled, expired, conflicting, or unavailable | Show fixed safe guidance and restart/email/current-session actions as appropriate | No additional access |

Ordinary email login remains its existing short path: email, code, authoritative Session. It does not require social authorization or a binding label. External binding must never send its code to the ordinary email verification endpoint or interpret an ordinary email Session as proof that provider binding succeeded.

## First-use Consent, Refresh, and Cancellation

1. After unbound authorization, remain outside protected library pages. Do not create or show a separate empty provider-owned library. Provider-supplied email/name/avatar claims neither preselect an account nor replace mailbox verification.
2. The code-entry form must identify the mailbox from the user's current in-memory input and explain that the provider will be bound to that mailbox's StashSeek library. Its **Verify and bind** action simultaneously supplies explicit consent and code proof. Do not hide consent in a generic login action, pre-submit confirmation, add an extra persistent `needs_confirmation` step, or introduce a separate binding endpoint.
3. Send exactly `{email, code, confirm_binding:true}`. The server normalizes and proves the exact email, chooses the account, commits the external mapping, consumes proof, and creates the Session atomically. The frontend draft is a consent display, not evidence of canonical ownership.
4. Existing and new email addresses receive the same accepted code-request response. Do not reveal whether the address already owns a library. A new email can create its email-owned account during successful atomic verification, without a preceding provider-only tenant.
5. Reloading `/login/external` restores only safe server flow status via the Cookie. Because the pending DTO does not return email or code, an absent local email draft requires re-entry and a fresh purpose-bound code request before showing the final action. There is no already-verified confirmation stage to restore. Never restore drafts through URL, router state, or Storage.
6. Cancel/change-method uses `DELETE /api/v1/auth/external/pending`; backend cancellation invalidates unfinished proof and rejects late callbacks. Clearing React state alone is insufficient. Cancellation does not log out an unrelated existing Session. If cancellation fails or its result is unknown, recheck pending state rather than claiming it succeeded.
7. Starting a different flow invalidates the previous browser flow. Switching to ordinary email must not accidentally finish a stale external flow; the backend cancellation/alternative-auth transition must make that separation explicit. Changing email requests a new challenge that invalidates prior code proof for that purpose.
8. Provider denial, browser Back, returning without a callback, and restoring a page from the back-forward cache must recheck safe server status, not automatically replay the native POST or leave its submit button permanently pending. Some providers may leave cancellation on their own page; expiry and manual return/restart remain valid outcomes.

## Existing Sessions, Concurrency, and Private Cache

- `/login` currently has no separate Session/flow coordinator. The new clean continuation must resolve current Session and flow outcome before choosing the binding form or library, so an unrelated cached Session cannot preempt a pending return.
- Start is allowed only in an unauthenticated browser. If a valid private Session already exists, retain it and show guidance to continue that library or explicitly sign out before another login flow. Do not silently replace an account or bind the provider to whichever Session Cookie is present. Adding a provider to an authenticated account would be a separately authorized settings feature.
- A deliberate account switch awaits successful logout before rotating/clearing private cache and starting external login. Failed logout preserves the current Session and library.
- If another tab establishes or changes a Session after an anonymous flow started, the backend must prevent the old callback/verification from silently binding to or replacing that account. Return a safe session-change result and recheck the current Session; the frontend must not treat any authenticated Session as proof that its pending binding completed.
- One active flow per browser is sufficient. Starting another invalidates the old context, including same-provider attempts. Without public flow IDs, server-side current-flow/claim rules must enforce this isolation. Local generation checks, AbortSignal, and disabled buttons only stop stale UI updates; they cannot enforce binding consistency or prevent late server responses from reviving old proof.
- On unmount, cancellation, provider/email change, or restart, cancel corresponding queries and discard stale interaction generations, old errors, and code drafts. A bounded status check on page return/refocus is reasonable; infinite terminal-state polling or private-library polling is not.
- A successful verification response may supply the authoritative canonical Session; a completed top-level login is validated by a fresh private Session request on the protected route. Never accept the authorization-before-login cached `["session"]` value as the new result. When verification succeeds, use the existing fresh-client activation boundary, copy only public capabilities with their original freshness, and remount private observers. Preload history only after Session validation and without blocking navigation.
- For an interrupted verification response, first read pending-flow outcome and authoritative Session. Accept completion only when the backend confirms the completed transaction and correct Session outcome; an unrelated Session is a session-change case. If proof was not consumed and the contract permits retry, retry safely rather than creating another account or repeating provider authorization blindly.
- Pending status is not persistently cached. Email/code mutation variables should have a short lifetime, such as `gcTime: 0`, and be removed on completion/cancel/unmount along with drafts. Cancel old private requests and ensure a late old 401, historical preload, or response cannot clear or repopulate a newly activated user's cache.

## Canonical Browser/API Contract

The following matches parent `design.md`. Endpoint implementation, final closed phases/error codes, Cookie details, and generated schemas remain foundation work.

| Operation | Canonical request | Frontend-visible contract |
| --- | --- | --- |
| Capabilities | Existing capabilities GET with `external_login_providers` | Configured/enabled `microsoft`, `wechat_website`, `qq`; no configuration secrets |
| Begin authorization | Native `POST /api/v1/auth/external/{provider}/start` | Exact Origin; backend flow Cookie; 303 directly to configured provider |
| Protocol callback | Backend `GET /api/v1/auth/external/{provider}/callback` | Backend validates/claims/exchanges; 303 to a fixed clean app destination; no SPA credential DTO |
| Inspect current flow | `GET /api/v1/auth/external/pending` | Closed safe status, provider, expiry; no email, identity IDs, flow handle, SDK material, state, code, or token |
| Request binding code | `POST /api/v1/auth/external/pending/challenges` with `{email}` | Generic accepted outcome; exact Origin and flow-bound CSRF; shared rate-limit behavior |
| Verify and bind | `POST /api/v1/auth/external/pending/verify` with `{email, code, confirm_binding:true}` | One transaction verifies proof, binds the exact anchor, consumes flow/challenge, and issues the normal Session |
| Cancel | `DELETE /api/v1/auth/external/pending` | Exact Origin and flow-bound CSRF; cancel and expire flow Cookies without ownership changes |
| Validate Session | Existing `GET /api/v1/auth/session` | Identifier-free canonical Session projection, with expanded login-method values |

Start is a native navigation exception to the JSON client path and uses the parent's exact-Origin contract. Pending mutations use the flow's separate double-submit CSRF value. Existing `requestJson()` reads the normal Session CSRF Cookie, so purpose-specific API wrappers must select the fixed flow-CSRF mechanism without teaching components to construct/retain credentials. Do not assume an anonymous browser already has a normal Session CSRF Cookie. Callback is the narrowly reviewed OAuth exception to the usual no-state-changing-GET rule, not a general frontend permission to add mutating GET APIs.

Generate FastAPI OpenAPI and TypeScript together from the canonical production composition. Extend `SessionResponse.login_channel` with the three external method names, while backend tenant identity remains the exact canonical email anchor and provider-session provenance is preserved. Frontend labels do not select tenant namespaces. Error DTOs retain bounded `{code,message}` fields; raw provider exceptions, error URIs, claims, or credential-bearing URLs must not be rendered or logged.

### Capability and Legacy Compatibility

- Preserve the meanings and existing values of `web_login_channels`. Add the separate canonical `external_login_providers` field and derive its frontend alias from generated schemas. Never map `wechat_website` to legacy `wechat` or pass an external provider to `createLoginChallenge()`.
- Distinguish checking, enabled, disabled/unconfigured, and unavailable due to network error. Unregistered/unconfigured providers are not usable fake buttons. Keep available email login reachable and never probe private deployment settings from the client.
- Email and external providers remain simultaneously reachable. If legacy methods are still enabled, distinguish chat-command login from website authorization so two unrelated WeChat methods do not have the same ambiguous label. Preserve legacy in-memory secret handling and terminal-state polling limits without reusing that protocol for website login.
- A new frontend reading an older capabilities payload treats missing `external_login_providers` as an empty list, keeping email/legacy usable. A failed capabilities request remains an honest failure rather than a fabricated all-disabled deployment.
- Audit every consumer of the expanded `SessionResponse.login_channel`, especially `AppShell`, tests, and generated fixtures. Use explicit display labels for Microsoft/WeChat website/QQ; backend email-anchor identity and public login-method labels have distinct responsibilities.

## Safe Error and Cancellation Behavior

| Condition | Frontend behavior |
| --- | --- |
| Capabilities or flow-state request fails | Preserve the known local stage, show retry, and do not invent an unbound/anonymous state |
| User denies/cancels provider authorization | Explain that sign-in was not completed; allow email or a fresh authorization; do not show provider internals |
| Expired, wrong-browser, replayed, or invalid callback | Fixed restart guidance without revealing which credential comparison failed |
| Provider/configuration unavailable | Safe explanation and email fallback; never replay an ambiguous consumed authorization code |
| Accepted mail request, rate limit, or uncertain delivery | Account-existence-indistinguishable feedback and backend-defined retry timing; no guaranteed-delivery claim |
| Wrong/expired code | Recoverable code-entry/resend/change-email behavior; not global `session_invalid` |
| External identity owned elsewhere or concurrent binding conflict | No override, tenant move, or merge; provide safe original-login/restart guidance |
| Disabled owner/anchor/provider | Stop sign-in and hide private data; do not create another account to bypass the restriction |
| Existing or changed private Session | Preserve the real Session, recheck it, and offer explicit continue/switch guidance rather than automatic binding |
| Verification result unknown | Reconcile pending outcome and authoritative Session before retrying; never infer success from an unrelated authenticated browser |
| Real `401 session_invalid` | Existing global private-cache teardown/rotation; a clean continuation cannot revive invalid private data |
| Unknown exception/provider error text | Fixed generic copy; no raw descriptions, stacks, URLs, or credential fields |

## Required Verification

1. Capability matrices: email only, partially enabled providers, external plus legacy, missing new field, and pending/error. Email must not hide enabled external methods; website WeChat must not call legacy challenge APIs.
2. Native start forms: exact fixed same-origin action and method, no frontend state/code/redirect construction, duplicate-submit handling, clean return, and Back/back-forward-cache recovery without replaying POST.
3. First use: provider proof, email request/code entry, clearly identified binding target, and **Verify and bind** submitting `confirm_binding:true`. No preverification access/history preload, no extra confirmation step, and no Session or partial mapping after failed proof/transaction.
4. Existing versus new email: same generic code-request feedback; existing library/history retained; new email creates only its email-owned account during the atomic completion.
5. Already-bound provider: direct protected-library destination and authoritative Session validation; no repeated mailbox challenge or provider-email/name-based matching.
6. Form/API errors: wrong code, resend/change-email, denial, expiry, unavailable provider, conflict, cancellation failure, and late cancelled callbacks; recoverable failures never trigger global Session teardown.
7. Reload/Back: restore safe flow status only; absent local email/code requires re-entry and a new purpose-bound challenge. Router state, SPA URL, Storage, logging, and analytics contain no flow credentials or code.
8. Concurrency: duplicate starts/verifications, same-provider attempts, cancellation followed by late promises, multi-tab Session change, and unknown verification result. Local stale results do not update a new flow; backend rejects proof from the superseded flow.
9. Existing Session A: continue A, deliberate switch to B, failed logout, another tab authenticating during a flow, and a late old callback. No silent replacement of A or binding B to A.
10. Cache activation: fresh QueryClient with only canonical Session/public capabilities, cancellation of old Session/history/stream work, late 401/result isolation, and nonblocking history preload only after verified authentication.
11. API wrappers/types: flow-bound CSRF for pending mutations, generated identifier-free DTOs, expanded explicit Session labels, and no accidental fallback of Microsoft/QQ to WeChat.
12. Accessibility/browser acceptance: labelled mailbox/code/binding action, announced progress/errors, reachable keyboard retry/cancel, stable focus, and complete narrow-screen operation. Real configured-provider and mailbox-delivery smoke is separate from fake-provider state-machine tests.
13. Run full frontend gates, OpenAPI stale checks, email/legacy/Telegram-link/logout/session-expiry regressions during implementation. This research does not create product tests or execute product builds; local demo success does not prove live provider registration, callback approval, or mail delivery.

## Superseded Alternatives

A JSON prepare request plus intermediate GET redirect, `/login/continue`, `web_social_login_providers`, a durable `needs_confirmation` stage with a separate binding endpoint/click, and a separate optional `login_provider` Session field are **noncanonical alternatives** from the earlier research. They are not part of the parent design or the proposed implementation and must not be introduced as parallel protocols.
