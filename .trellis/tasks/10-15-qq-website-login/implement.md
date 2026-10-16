# QQ Execution

1. Confirm parent approval, stable foundation, contexts and current official QQ protocol/application docs before starting.
2. Implement isolated adapter/config and strict bounded response parsing; verify AppID/OpenID and redact outbound errors/query strings.
3. Test first/repeat login, state/flow failures, wrong client, malformed JSON/form/JSONP, provider errors, discarded tokens, disabled config and mapping conflicts.
4. Write registration/review/callback/private-config guide with no secrets/unverified eligibility claims.
5. Run provider/shared auth/session and parent contract/gate checks; review parsers/credential-bearing requests independently.
6. Run real smoke only after registration and separately authorized live configuration/deployment. Record Q1–Q5 code/live evidence separately.

Rollback disables QQ/revokes its sessions and retains schema/mappings. No tenant merge is introduced here.

## Local Verification Status

Local code and synthetic/isolated tests are implemented and independently reviewed. See [parent verification](../10-15-multi-provider-login/verification.md) for exact commands, results, known unrelated failures and browser proof. Real provider registration and deployed acceptance are still pending; this task is not archived or represented as live-ready.
