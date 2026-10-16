# Website Login State and Private Cache Lifetime

## 1. Scope / Trigger

Apply when changing `/login`, `/login/external`, `LoginRoute`, external-method
forms, `ExternalLoginPage`, API auth wrappers or private QueryClient rotation.
Read backend `external-login.md` for the authoritative proof/session contracts.
The local demo is not evidence of live provider authorization or real mail.

## 2. Signatures

```typescript
probeLoginSession(signal?: AbortSignal): Promise<SessionInfo | null>;
getExternalLoginPending(signal?: AbortSignal): Promise<ExternalLoginPending>;
requestExternalLoginCode(email: string, signal?: AbortSignal): Promise<AcceptedResponse>;
verifyExternalLogin(input: ExternalLoginVerifyInput, signal?: AbortSignal): Promise<SessionInfo>;
cancelExternalLogin(signal?: AbortSignal): Promise<void>;
```

`requestJson()` has internal request context
`{csrf?: "session" | "external", unauthorized?: "global" | "local"}`.
Only the login Session probe uses local unauthorized handling; pending mutations
select external CSRF. Do not make components read or retain either cookie.

`ExternalLoginProvider`, pending response and verification input are aliases of
generated schemas. `external_login_providers` is a capabilities field; the
fallback for an older capabilities response missing it is an empty list.

## 3. Contracts

- Email and enabled external methods remain simultaneously reachable. Preserve
  legacy `web_login_channels`; website `wechat_website` never calls the messaging
  challenge API. Explicit method labels cover Microsoft, website WeChat and QQ.
- External start is a native POST form to the fixed same-origin
  `/api/v1/auth/external/{provider}/start`, followed by server 303 navigation.
  Frontend code does not construct provider state/code/redirect URLs. Disable
  duplicate submits and reset transient start state on `pageshow`/Back recovery.
- `/login/external` is a clean anonymous continuation route. Load safe pending
  state and `probeLoginSession()`; never require an authenticated protected layout
  before the first-use email form can appear. Expected probe
  `401 session_invalid` returns null locally, without global navigation to
  `/login` or consuming/cancelling the pending flow. Other errors remain errors.
- `LoginRoute` clears old private queries/mutations and rotates the QueryClient
  when an anonymous result discovers prior private data. Capture only a valid
  public capabilities DTO and its original freshness timestamp. Identify the
  private cache present before the route mounts, so freshly mounted login
  observers do not cause an endless clear/remount/probe loop.
- Successful verification, or a completed callback validated by a fresh Session,
  uses the same activation boundary: clear/dispose the previous client, create
  the next client seeded with canonical Session plus public capabilities only,
  remount observers and replace-navigate to the library. Never transfer history,
  transcript, library or private mutation data. Protected requests retain global
  invalid-Session teardown. Abort/ignore stale probes and late old 401s/results.
  The JSON client captures its unauthorized handler at request start and invokes
  it only if that owner is still current, including mutations without a signal.
- A pending response can expose only closed status/provider/expiry/error fields.
  Session exposes only authenticated/method/expiry. Neither contains IDs,
  mailbox, raw state/code/flow handle or tokens. Credentials never enter React
  Router state, Web Storage, persistent query cache, analytics or logging.
- Provider first use displays the mailbox's role in selecting the library and
  submits `{email, code, confirm_binding:true}` through **验证并绑定**. No extra
  durable post-verification confirmation stage exists. Email/code drafts and
  mutation variables stay in memory (`gcTime:0`); refresh may require re-entry.
- Verify/retry/cancel transitions use AbortController and interaction generations
  so superseded responses cannot bind UI state to another flow. No infinite
  terminal polling: page focus/`pageshow` or explicit retry rechecks safe status.
  `created`/`exchanging` can show waiting/recheck/cancel guidance.
- On uncertain final-submit outcome, fetch pending status and the authoritative
  Session before retry. A completed flow alone, an unrelated logged-in Session,
  or an equal provider label alone cannot establish completion; rely on the
  backend's completed-Session match. Completed binding without delivered Cookie
  restarts provider sign-in; consumed proofs are not resubmitted.
  Read the Session first, then request pending state so the backend checks the
  Cookie visible after the probe. Parallel reads can incorrectly pair an old
  completed flow with another account using the same provider label.
- Existing/changed Session prompts explicit continue or successful logout before
  restart. Failed logout preserves the current account. A wrong verification
  code is a local form failure, not a global `session_invalid` event.
  The ordinary email endpoint also cancels the cookie-selected external flow
  transactionally; frontend aborts/generation guards alone cannot prevent a
  provider callback already waiting on network from issuing a stale Session.
- Changing the ordinary login mailbox or restarting a legacy login increments
  the interaction generation before resetting mutations. Resetting an observer
  alone does not stop its already-running success callback from activating.
- Generate OpenAPI and TypeScript from canonical FastAPI together. Expanded
  `SessionResponse.login_channel` consumers, including `AppShell`, must use
  explicit labels rather than treating every unknown method as WeChat.

## 4. Validation & Error Matrix

| Condition | UI/cache result |
| --- | --- |
| Missing/disabled provider | No usable external form; retain available email/legacy entries. |
| Capability/Session/pending network failure | Honest checking/retry error; do not invent anonymous or all-disabled status. |
| Anonymous probe returns `session_invalid` | Stay on the current login/continuation route; dispose pre-existing private cache once. |
| `verification_failed` | Recoverable code entry/resend; no global Session teardown. |
| Expired/cancelled/failed flow | Clear sensitive drafts and show fixed restart/email guidance. |
| `session_present` / `session_changed` | Preserve actual Session; explicit continue or sign out. |
| `binding_conflict` | Fixed original-login/restart guidance; no overwrite or client-selected owner. |
| Verification response lost | Reconcile flow plus Session; do not blindly submit consumed proof again. |
| Old request completes after cancellation/rotation | Abort/ignore; never clear, navigate or populate the current account's cache. |
| Valid protected request gets `session_invalid` | Existing global private teardown and fresh login transition. |

## 5. Good / Base / Bad Cases

- Good: `/login/external` receives an anonymous probe, discards old account data,
  preserves public capabilities, then allows email proof without route loops.
- Base: capabilities have no external providers; existing email and supported
  legacy login behave as before.
- Bad: use protected `getSession()` as the anonymous probe, rotate on every
  newly mounted login observer, seed a new account with old private queries, or
  trust a Session returned before the pending flow completed.

## 6. Tests Required

- `web/src/auth/LoginPage.test.tsx`: provider/email/legacy capability matrices,
  fixed native form actions, duplicate starts, explicit current-Session choice,
  failed logout and draft/storage boundaries.
- `web/src/auth/ExternalLoginPage.test.tsx`: first-use proof/confirmation,
  wrong code, cancellation/reload/stale requests, uncertain completion, completed
  without delivered Session, changed Session and bounded recheck behavior.
- `web/src/app/App.test.ts`: real route integration with prior private cache,
  one-time anonymous cleanup, preservation of public capabilities, continuation
  route stability and successful activation. Include stale 401/result isolation.
- `web/src/api/client.test.ts`: flow CSRF versus normal Session CSRF; expected
  anonymous probe 401 is local while protected invalidation stays global.
- Run `pnpm generate:api`, tests, typecheck, lint, build and `pnpm check:api`.
  Browser smoke covers native redirects, clean return, narrow layout, labels,
  keyboard/cancel/retry and empty credential storage. Live provider readiness is
  a separate registered-domain acceptance step.

## 7. Wrong vs Correct

### Wrong

```typescript
await getSession(); // anonymous 401 invokes global login navigation
activateSession({ ...oldCachedSession });
requestJson("/api/v1/auth/external/pending/verify", {method: "POST", body});
```

### Correct

```typescript
const session = await probeLoginSession(signal); // local expected 401 -> null
const flow = await getExternalLoginPending(signal);
// Only the owning LoginRoute activates a verified result and rotates its cache.
requestJson("/api/v1/auth/external/pending/verify", {method: "POST", body, signal},
            {csrf: "external"});
```
