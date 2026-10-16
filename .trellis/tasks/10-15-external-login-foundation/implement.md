# Shared Foundation Execution

## Start Gate

- [x] User approved the completed parent planning summary with “开始” on 2026-10-15; contexts validated and foundation selected for implementation.
- [x] Prior WIP snapshot stored under /private/tmp/stashseek-before-external-login; implementation agents load curated specs/design/research.

## Ordered Work

1. Add schema/migration and test owner FKs, provenance, compatible old rows, and a single head.
2. Extract transaction-local email challenge/account/session helpers; test unchanged email behavior, failed-attempt persistence, correctly placed savepoints and proof-purpose separation.
3. Implement browser-flow lifecycle, safe API/errors, native start redirect, callback claim, flow Cookie/CSRF, bounded retention and test-only provider injection.
4. Implement atomic binding, repeat login, uncached provider-session validation, explicit email-cache admission/version and disabled/revoked-state tests.
5. Integrate mapping migration with explicit merge; guard mapped-anchor rebind. Test ownership/concurrency/revoked absorbed sessions.
6. Implement shared frontend entries/continuation/code-and-binding form, clean native-start failure redirects, explicit flow-CSRF context, login-only anonymous Session probe, cancellation/recovery, Session checks, QueryClient rotation and method labels; regenerate API/types. Preserve global invalid-session handling on protected requests.
7. Run focused tests, isolated PostgreSQL races/migration checks, full frontend gates and real-browser fake-provider navigation. Record live prerequisites honestly.

Use parent validation commands/rollback gates. Intended new tests include `test_external_login.py` and `test_external_login_routes.py`; reuse current auth/session/linking tests. No live credentials, external sends, production migrations, commits or deployment without applicable authorization.

## Local Verification Status

Local code and synthetic/isolated tests are implemented and independently reviewed. See [parent verification](../10-15-multi-provider-login/verification.md) for exact commands, results, known unrelated failures and browser proof. Real provider registration and deployed acceptance are still pending; this task is not archived or represented as live-ready.
