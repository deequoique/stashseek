# External Login Verification — 2026-10-15

## Delivery Status

The approved local implementation is complete. Microsoft, WeChat website and QQ
adapters share an email-anchored first-use binding flow. Existing email login
continues to accept valid mailbox domains. Repeat bound-provider login resolves
the original owner without another mailbox code. No provider-only library is
created, and provider email claims or names do not select an account.

All tasks remain `in_progress` because real applications, private server
configuration and deployed-provider acceptance are still missing. No commit,
push, production migration or deployment was performed. The prior dirty worktree
snapshot is at `/private/tmp/stashseek-before-external-login`; previous chat,
history, UI and email-performance work was preserved.

## Automated Evidence

| Gate | Result |
| --- | --- |
| Full frontend `pnpm test` | **205 passed** after independent review fixes |
| `pnpm typecheck`, `pnpm lint`, `pnpm build`, `pnpm check:api` | Passed; current production assets rebuilt |
| Provider protocol tests | **27 passed**, using real MSAL and signed synthetic Microsoft tokens plus mocked provider HTTP |
| Broad focused backend auth/API/config/legacy/ownership tests | **198 passed, 1 skipped**; the skip is the old full-history migration test without a configured PostgreSQL URL |
| `tests/test_external_login_postgres.py` on isolated PostgreSQL 17.9 | **10 passed**, including the new additive migration roundtrip |
| Final full backend suite | **953 passed, 84 skipped, 2 failed**; failures detailed below |
| Alembic heads | Exactly `e2f3a4b5c6d7` |
| Python compilation and `git diff --check` | Passed |

The full suite used no real API keys, disabled all external providers and used
an intentionally unavailable test database address. HTTP test fixtures were
allowed to bind only their local test servers. PostgreSQL tests separately read
an explicitly isolated test URL, created UUID schemas and dropped only those
schemas. No production database was used. The temporary PostgreSQL and browser-QA
servers have been stopped.

The PostgreSQL coverage includes first binding and repeat resolution, concurrent
provider/email creation, conflicting bindings without orphan owners, one callback
claim, explicit merge preserving videos/history/exact email anchors, absorbed
session revocation, CLI rebind rejection, and email-login/callback response order.
The additive migration was exercised against the predecessor auth schema with
old web/Telegram/WeChat session rows. This is not a claim that the entire historical
ingestion/vector migration chain was run; this temporary PostgreSQL build does
not provide `pg_trgm`.

### Existing full-suite failures

These two existing tests seed deletion dates in August 2026, then invoke
`IngestSubmissionService.submit_urls`, which reads the real current date. On
2026-10-15 those dates are outside the 30-day retention window, so the correct
retention guard returns `purge_in_progress` while the old tests expect `restored`:

- `tests/test_item_management_tools.py::test_save_restore_early_return_is_committed_and_retry_queue_failure_is_retryable`
- `tests/test_item_management_tools.py::test_delete_restore_and_resave_converge_on_same_item`

Neither that test file nor `app/ingest/submission.py` was changed for SSO. These
unrelated date-dependent failures were recorded, not hidden or bypassed. The
full-suite skips include other existing integration tests requiring their own
database/runtime setup. MSAL emits 14 query-response-mode warnings; this flow
deliberately uses a top-level GET callback with a SameSite=Lax flow Cookie.

## Independent Review Corrections

- Require the Microsoft signing key's issuer and verify its tenant relationship;
  reject missing issuer instead of assuming a match.
- Preserve blank QQ form fields so duplicate credential fields cannot disappear
  during parsing.
- Add only enabled providers' fixed authorization origins to CSP `form-action`.
- Successful ordinary email login locks and invalidates the browser's pending
  external flow in the same transaction, revoking its prior provider session
  when necessary. A delayed callback cannot revive it; failed proof leaves it
  available. Delayed provider errors cannot overwrite terminal flow states.
- Continuation reads Session before pending status so the final server check
  validates the current browser Cookie against the completed flow.
- Changing mailbox/login method invalidates old UI interaction generations;
  unmounted or stale responses cannot activate a prior session.
- Anonymous login probing clears and rotates old private queries/mutations once,
  preserving public capability freshness and remaining on the continuation page.
  An old request's late 401 cannot call a newer client's unauthorized handler.

## Browser Evidence — Synthetic Providers Only

An isolated canonical API with an in-memory database and fake identity providers
ran on port 8001, with a separate frontend on `http://localhost:5174`. Its private
test mail sender never sent email. Test-only injection was stored solely under
`/private/tmp`, never in product runtime/configuration. Secure `__Host-` Cookies
were retained on the Chromium localhost test origin.

Verified in the in-app browser:

- Native Microsoft POST → synthetic authorization → callback → clean
  `/login/external` URL, followed by explicit mailbox verification and binding.
- Wrong code stays on the form; a valid code enters `/library` with a truthful
  Microsoft account-menu label.
- Logout followed by Microsoft authorization enters the library directly without
  a second mailbox code.
- WeChat website authorization reaches its separate binding page; cancel returns
  to login without a session.
- QQ binds through its own page using the same test mailbox and shows the QQ
  login label.
- Login layout at 390×844 has no horizontal overflow and all methods are usable.

Screenshots show enabled **synthetic test providers**, not live account readiness:

- [Desktop test login](./artifacts/login-test-desktop.jpg)
- [390px test login](./artifacts/login-test-mobile.jpg)

The test tab was closed and the viewport override reset. The user's original
`http://127.0.0.1:5173/login` tab was retained. Its stopped demo API and Vite
processes were restarted on the original ports 8000/5173. That demo remains a
read-only, in-memory product demonstration and does not perform real SSO, email
delivery or video ingestion. Missing provider configuration hides provider buttons.

## Remaining Live Acceptance

1. Register and obtain approval for Microsoft, WeChat website and QQ applications.
2. Configure their exact HTTPS callbacks and private server credentials, along
   with a real email sender, migrated PostgreSQL and canonical backend.
3. With separately authorized deployment, verify each provider's first binding
   to an existing library, repeat access to its videos/history, logout and failure
   recovery on the registered domain.

Use [the configuration guide](../../../docs/how-to/configure-external-login.md).
Do not send secrets in chat. Code tests and fake-provider browser checks do not
replace registration, real mail delivery or deployed-provider smoke tests.
