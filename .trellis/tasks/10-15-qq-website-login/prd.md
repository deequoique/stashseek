# QQ Website Login

## Goal and Scope

Support QQ website authorization through shared first-use email binding and repeat login, preserving the selected email library/history. Deliver parent R1 and QQ-specific R4–R10.

## Acceptance

- [ ] Q1: Configured QQ authorization completes email binding and repeat direct login into the same account.
- [ ] Q2: State/browser mismatch, replay/expiry, malformed responses, invalid OpenID or mismatched AppID cannot bind/issue a session.
- [ ] Q3: App-scoped OpenID never acts as an email, Bot identity or cross-account merge key.
- [ ] Q4: Incomplete/disabled configuration has no usable entry; bounded errors expose no AppKey/code/token.
- [ ] Q5: Official application review/domain/callback guidance and redacted real smoke are documented separately from fake tests.

## Dependency and Exclusions

Depends on stable `10-15-external-login-foundation` contracts. No sharing/contacts/profile sync/mail access, UnionID merge or independent QQ-only library. Real registration/credentials are pending.
