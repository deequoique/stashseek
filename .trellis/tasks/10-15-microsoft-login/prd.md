# Microsoft Website Login

## Goal and Scope

Support personal Microsoft accounts and organizational accounts permitted by their organization. Reuse shared first-use email binding and direct repeat login, preserving existing libraries/history. Deliver parent R1 and Microsoft-specific R4–R10.

## Acceptance

- [ ] M1: Configured authorization completes first-use email binding into that mailbox's existing/new library; repeat authorization enters the same library without a code.
- [ ] M2: Wrong state/nonce/signature/audience/issuer, invalid times/algorithm, missing subject and malformed/duplicate callback fields cannot bind or issue a session.
- [ ] M3: Provider emails/names or different tenant subjects never auto-merge; stable identity uses validated issuer/application scope.
- [ ] M4: Incomplete/disabled config is unavailable; safe decline/retry and session-provenance policy work with no token/secret leaks.
- [ ] M5: The official registration guide specifies account types, Web callback, private credentials and redacted real smoke; fake tests and live readiness remain separate.

## Exclusions and Dependency

Depends on `10-15-external-login-foundation` reviewed contracts. No Graph/mailbox access, refresh-token feature, directory/group provisioning, SAML or new merge UI. Registrations/credentials are not yet available; automated tests cannot prove real acceptance.
