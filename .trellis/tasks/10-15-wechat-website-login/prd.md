# WeChat Website Login

## Goal and Scope

Add WeChat website authorization with shared first-use email binding and repeat login. Keep website identities distinct from existing WeChat messaging/Bot login. Deliver parent R1 and WeChat-specific R4–R10.

## Acceptance

- [ ] W1: Approved/configured website application completes email binding and repeat login into the same email-owned library.
- [ ] W2: Wrong browser/state, replay/expiry, malformed token/identity, mismatched application scope and disabled config fail without a binding/session.
- [ ] W3: App-scoped OpenID never automatically links a Bot identity, nickname, email or unrelated app identity. UnionID does not trigger merge.
- [ ] W4: Rejection/absence of callback expires safely and permits restart; no unsupported callback behavior or fake working entry is promised.
- [ ] W5: Current official protocol/security-mode and registration prerequisites are reviewed/documented before adapter implementation; live smoke remains distinct from fakes.

## Dependencies and Exclusions

Depends on stable shared foundation and current official-source evidence in parent research. No Official Account authorization, special embedded-WeChat-browser flow, Bot redesign, cross-app UnionID merge or self-service merge. Website application approval/credentials are pending; eligibility is not assumed.
