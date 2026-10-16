# Implementation and Integration Plan

## Planning Gate

- [x] User approved task creation and specified Microsoft, WeChat, QQ, and mailbox-independent email codes.
- [x] User chose email-anchored first use, preserving the existing library; all product decisions in the PRD are resolved.
- [x] Final planning artifacts were presented; user explicitly approved implementation with “开始” on 2026-10-15. Approval persists after the interrupted turn.
- [x] Confirm each child has reviewed requirements, design, execution plan, and real curated context entries. The parent remains an integration task, not a substitute for child validation.

## Execution Order

1. **Shared foundation:** `.trellis/tasks/10-15-external-login-foundation`. Snapshot the existing WIP diff; establish schema, provider protocol, purpose-bound email proof, atomic binding/session issuance, safe callback lifecycle, session provenance/DB checks, merge/rebind compatibility, and frontend UX using a test-only fake provider. Production capabilities advertise no fake provider.
2. **Microsoft:** `.trellis/tasks/10-15-microsoft-login`. Implement the validated OIDC adapter and registration/configuration guide against the stable foundation. Pin maintained libraries and test signed tokens, claims, state/nonce/PKCE, issuer templates, and rotations.
3. **QQ:** `.trellis/tasks/10-15-qq-website-login`. Implement fixed-endpoint OAuth/OpenID exchange and strict parser, AppID checks, safe errors, and registration guide. This may run alongside Microsoft only after the foundation contracts stabilize and agents have disjoint provider files/tests.
4. **WeChat:** `.trellis/tasks/10-15-wechat-website-login`. First complete/review current official-source protocol evidence, including security-mode requirements and cancellation behavior, then implement the adapter and registration guide. Do not substitute an incomplete generic OAuth adapter for this gate.
5. **Parent integration:** review all login methods together, existing email and Telegram linking compatibility, private-data isolation, generated API agreement, configuration combinations, real browser navigation, and rollout instructions. Verify each child acceptance map and classify live-provider prerequisites explicitly.

## Validation Gates

For shared authentication changes, run targeted existing and new tests with the local virtual environment, including:

```text
.venv/bin/python -m pytest tests/test_web_email_auth.py tests/test_web_session_cache.py tests/test_web_api_auth.py tests/test_web_api_runtime.py tests/test_web_auth_models.py tests/test_web_auth_migration.py
.venv/bin/python -m pytest tests/test_external_login.py tests/test_external_login_routes.py
```

New file names above are intended locations to be created, not tests already present. Add focused PostgreSQL integration tests for duplicate email/provider creation, replay and ownership conflicts, merge behavior, and migration roundtrip. Execute them on an isolated local/test PostgreSQL database following the existing PostgreSQL fixture configuration; SQLite/fakes do not establish row-lock or concurrency correctness. Run the relevant existing multiuser/linking tests after those shared paths change.

Frontend gates from `web/`:

```text
pnpm generate:api
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm check:api
```

Run `git diff --check`, verify a single Alembic head, and run the full backend suite once the focused changes pass and all provider/ownership paths are integrated. Do not run live DB migrations, external authorizations, or mail sends merely to satisfy a unit-test gate.

Real-browser checks use a temporary test application with injected fake provider/HTTPS loopback as appropriate, without replacing the user's demo data. Verify top-level POST/redirect flow, clean callback return URL, first-bind code/resend/cancel/refresh, repeat direct login, expired/missing/wrong-browser flow, method visibility, keyboard access and narrow layout. Confirm no provider credentials enter browser storage, route state, logs, screenshots, or private-query cache.

Real-provider smoke requires the user's registered applications, approved domain/callbacks, private server configuration, a real email sender, and a separately authorized deployment. Test first-use binding to an existing library, repeated login to the same owner, logout, and unavailable/cancelled flows for each provider. Record only redacted outcomes, never codes, identity mappings, or tokens.

## Review and Rollback Points

- Review additive schema and transaction helpers before connecting any provider adapter. Check exact email-anchor ownership, unique races, and provenance deletion behavior.
- Review merge/rebind changes with provider-session tests before enabling provider authentication.
- Review protocol/claim verification and logging redaction per adapter before any live credential use.
- Keep provider feature flags off until that provider's configuration and live acceptance are valid.
- Revoke provider sessions before an application rollback that cannot understand their provenance; retain additive schema and data.
- Preserve all prior uncommitted auth/history/chat/CSS/schema work. No commit, push, production migration, or deployment is part of this planning approval.

## Completion Evidence

Record commands/results, redacted browser outcomes, and per-provider code/live status in this task's verification record. Update actual implemented specs after code passes review; planning alternatives must not be written into authoritative specs as already-implemented behavior. The parent is not complete while required live-provider acceptance remains unverified.
