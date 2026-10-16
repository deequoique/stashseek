# Microsoft Execution

1. Confirm parent final-plan approval, stable shared-foundation contracts, contexts and current primary-source SDK/token docs before starting.
2. Pin maintained libraries; implement isolated adapter/config, bounded metadata/JWKS, redacted safe errors.
3. Test signed synthetic tokens/mocked HTTP for M1–M4, key rotation, multi-tenant issuers, state/nonce/PKCE, malformed/conflicting claims, disabled config and token discard.
4. Write official registration/callback/private-credential guide and distinguish code-ready/live-ready evidence (M5).
5. Run focused provider plus shared auth/routes/session regressions, API stale checks and parent gates; independently review signature/claim validation.
6. With real registration and separately authorized deployment/configuration, perform redacted first-use/repeat/logout smoke; otherwise keep live readiness unverified.

Rollback follows parent provider-disable/session-revoke policy, retaining schema/mappings. Do not change shared API shape without parent review.

## Local Verification Status

Local code and synthetic/isolated tests are implemented and independently reviewed. See [parent verification](../10-15-multi-provider-login/verification.md) for exact commands, results, known unrelated failures and browser proof. Real provider registration and deployed acceptance are still pending; this task is not archived or represented as live-ready.
