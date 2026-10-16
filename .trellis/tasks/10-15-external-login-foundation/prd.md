# Shared External Login and Email Binding

## Goal and Confirmed Decisions

Provide the reusable website authorization, email-binding, and session boundary for the parent multi-provider-login task. The user chose first-use email verification, explicit binding to that mailbox's existing/new account, and direct bound-provider sign-in later. No provider-only library is created.

## Scope

- Deliver parent R2–R10 shared behavior: browser-bound flow, email proof, atomic binding, truthful capabilities/session DTOs, login UI, revocation, and ownership compatibility.
- Retain the exact canonical email `ChannelIdentity` and `AppUser` ownership of existing library/history data.
- Test through dependency-injected fake adapters restricted to tests; advertise no fake provider in production.
- Preserve existing email, logout, cache teardown, legacy messaging login, and Telegram linking behavior.

## Acceptance

- [ ] F1: First fake-provider authorization requires its own email code and explicit binding before any account/session is created; existing-email completion retains its original library.
- [ ] F2: Repeat bound-provider login needs no email code and resolves the same enabled owner/anchor.
- [ ] F3: Replay, wrong browser/state/provider, expiry, code-purpose mismatch, changed Session, and ownership conflicts cannot bind or issue a session.
- [ ] F4: Concurrent registration/binding yields one owner and mapping, no orphan candidate user, and atomic rollback on failures; prove locking/conflicts against isolated PostgreSQL.
- [ ] F5: Provider-provenance sessions bypass Redis and reject disabled/mismatched owner, anchor, mapping, or configuration. Provenance cannot be deleted/downgraded into an email session.
- [ ] F6: Existing explicit merges preserve mappings and anchors, revoke absorbed sessions, and guarded CLI rebind cannot transfer provider access.
- [ ] F7: Login forms, safe errors, cancellation/recovery, generated contracts, and email/legacy regressions pass; private cache never survives account change.

## Exclusions and Dependencies

No live provider adapter, live credentials, deployment, self-service unlink, mailbox change, or silent tenant merge. The known legacy cross-process email-cache limitation remains separately documented; new provider sessions must be secure independently of it.

No code dependency on another child. Read the parent's PRD/design and shared-auth/frontend research. Parent AC2–AC6 and AC8–AC9 are established here against fake providers; real AC1/AC7 remains provider/integration acceptance.
