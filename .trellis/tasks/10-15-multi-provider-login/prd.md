# Multi-provider Website Login

## Goal

Let people sign in to StashSeek with Microsoft, WeChat, QQ, or an email code while retaining access to their own saved videos, collections and search history.

## Background and Confirmed Decisions

- On 2026-10-15 the user requested these four entry methods, approved a new Trellis planning task, confirmed that no external-provider application registrations exist yet, and selected option 1: first-time provider authorization requires email verification and explicit binding; later bound-provider sign-in enters the same library directly.
- Existing email-code registration/login accepts valid addresses without a mailbox-domain allowlist (`app/web_auth.py:576`, `app/web_auth.py:600`); delivery depends on the deployed email sender.
- Existing `wechat` identifies messaging/Bot login, not website OAuth. New website login must remain distinct.
- The local in-memory demo cannot prove real provider authorization, mail delivery, durable accounts or ingestion. No production deployment is implied by this task.

## Requirements

| ID | Requirement |
| --- | --- |
| R1 | Offer Microsoft personal/organizational accounts (subject to organization policy), WeChat website login, QQ website login, and email codes. |
| R2 | Use a common code flow across mailbox services; do not require separate OAuth for every domain or read user mail. |
| R3 | On first provider use, verify an email and explicitly bind to its existing/new account without first creating a provider-only library. Repeat use opens that same library without another code. Preserve original video/collection/history ownership. |
| R4 | Prove control of both identities before binding. Provider email/name/avatar or a matching external string alone cannot grant library access. |
| R5 | Repeated sign-in resolves the same account. An identity already owned by a different account fails safely rather than being reassigned or silently merged. |
| R6 | Offer usable buttons only for completed, enabled, configured providers. Cancelled, expired, rejected or unavailable authorization has safe retry guidance. |
| R7 | Preserve current email/legacy login, logout, expiry, supported disabled-account behavior, tenant isolation and private-cache teardown. |
| R8 | Include platform registration/configuration and real-provider acceptance guidance; never request secrets in chat or commit them. |
| R9 | Explain that the verified mailbox selects the library and label the final code action as binding. Incomplete/cancelled/expired binding grants no access and can restart. |
| R10 | Provider-issued sessions retain verified login provenance and reject a disabled mapping, email anchor, owner or provider configuration. |

## Acceptance Criteria

- [ ] AC1 (R1, R6): Each configured provider returns an authenticated browser to the protected library. Disabled/incomplete providers cannot initiate a misleading flow.
- [ ] AC2 (R2, R7): Existing email login and first-time registration continue for valid mailbox addresses with preserved delivery-failure/retry behavior.
- [ ] AC3 (R3, R5): All bound methods repeatedly open the same library/history without moving existing ownership.
- [ ] AC4 (R4, R5): An unverified provider email/name cannot access an existing account. Conflicts cause no reassignment, partial binding or implicit merge.
- [ ] AC5 (R4, R6): Wrong-browser, expired, replayed, cancelled or invalid callbacks cannot bind or create a session.
- [ ] AC6 (R7, R10): New provider sessions observe logout/revocation/disabled owner, anchor, mapping and policy through database validation independently of Redis invalidation. Existing supported email/logout/cache regressions pass, and account changes cannot show another account's cached data. The pre-existing standalone-CLI cross-process email-cache limitation is recorded separately and is not claimed fixed.
- [ ] AC7 (R8): Fake-provider tests and real deployed-provider smoke evidence are reported separately; missing registrations remain explicit rollout prerequisites.
- [ ] AC8 (R3, R9): First-time completion requires this flow's email proof and a single explicit Verify-and-bind action; before it completes no independent library or session is created. Bound repeat login needs no additional code.
- [ ] AC9 (R5, R10): Concurrent first-use attempts cannot split owners or rebind identity keys. Existing explicit linking preserves valid provider mappings/anchors and revokes absorbed-account sessions.

## Out of Scope

- Enterprise SAML, directory/group provisioning, arbitrary custom identity providers, and special embedded-WeChat/Official Account authorization.
- Per-mailbox OAuth, reading mail, social sharing/profile sync or cross-app UnionID merges.
- Provider-only accounts, self-service unlink/email replacement, new account-merge UI, silent ownership changes, and Bot/Telegram linking redesign. Existing supported explicit linking remains compatible.
- General repair of every legacy cache/merge capability, video-ingestion enablement, production publishing, migrations or deployment of earlier local UI changes.

## Delivery Map

| Child | Deliverable | Dependency |
| --- | --- | --- |
| `10-15-external-login-foundation` | Browser flow, atomic email binding, session/ownership compatibility and common login UI | None; test-only fake adapter |
| `10-15-microsoft-login` | Microsoft adapter and registration guide | Stable shared foundation |
| `10-15-qq-website-login` | QQ adapter and registration guide | Stable shared foundation |
| `10-15-wechat-website-login` | WeChat website adapter and registration guide | Stable shared foundation and reviewed current official protocol |

The parent owns AC1–AC9 integration acceptance. Every child reports code/test readiness separately from live readiness. The parent is complete only when all required provider and cross-method acceptance passes; fakes alone are insufficient.

## Planning and Rollout Status

Product decisions are resolved. This converged PRD, the technical `design.md`, execution `implement.md`, child artifacts and curated contexts form the reviewable plan. Research records repository constraints and official sources; registrations/credentials/domain approval, real mail delivery and live smokes remain rollout prerequisites.

The user approved the final plan with “开始” on 2026-10-15. All five tasks are now `in_progress`: local implementation, independent reviews and credential-free verification are complete. Real application registrations, deployment configuration and live-provider acceptance remain outstanding; the parent is not complete. No production migration, commit, push or deployment has been performed. See [verification.md](./verification.md) for code evidence and limitations.
