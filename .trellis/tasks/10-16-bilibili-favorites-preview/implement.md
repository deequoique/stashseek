# Execution plan — public favorites and part-selection UI

Planning only. Do not run `task.py start` or product implementation until the
parent's complete final plan is approved. Preserve all existing SSO/BV/share/chat
work; never reset it, commit it as this task, or deploy as part of verification.

## 0. Dependency and contract gate

- [ ] Verify `.trellis/tasks/10-16-bilibili-multipart` foundation completion and
  its tests for stable BV/CID source, immutable pin/retry, tenant-bound signed
  part refs, ≤10 parts admission/unchanged quotas, source resolver and additive
  legacy-safe migration. Do not implement a competing foundation in this child.
- [ ] Verify the frozen routes against the completed foundation:
  `POST /api/v1/bilibili/parts:preview`, `POST /api/v1/bilibili/parts:batch` and
  owned `GET /api/v1/library/items/{item_public_id}/source?t=...`. Consume its
  generated response enums, stable `part_key`, 30-minute `part_ref` expiry and
  `has_unverified_legacy_item` compatibility field, without a second contract.
- [ ] Confirm composition/capability gate and shared bounded discovery budgets.
  Retain all existing ingestion quota and worker settings and read-only demo.
- [ ] Capture current WIP baseline and load curated specs/context; dispatch
  Trellis implement/check roles from the main session only.

## 1. Bounded favorites read service

- [ ] Add a feature-scoped strict local parser for supported `space/.../favlist`
  and known `medialist/detail/ml` form only; use no user-provided fetch target.
- [ ] Implement one-page favorites operation atop the foundation discovery
  transport: fixed HTTPS host/path, `ps=20`, bounded page 1–500, 1 MiB and a
  15-second whole-call deadline, TLS, no redirects/credentials, shared preview
  concurrency/rate guard and fixed safe errors.
- [ ] Project bounded collection/page/parent DTOs; mark invalid/unavailable rows
  safely, do not fetch all rows' parts, do not create content/dispatches.
- [ ] Add canonical `POST /api/v1/bilibili/favorites:preview` with body
  `{url,page=1}`, exact Origin/Session-CSRF, no-store, explicit runtime/service
  wiring and capability availability. No new app/session/cookie or migration;
  collection URLs remain out of request query strings/access logs.
- [ ] Add isolated transport/parser/service/API tests: supported aliases,
  tracking/ambiguity, host/port/fragment/credential attacks, redirects, body/time/
  page/count caps, private/deleted/risk-control/malformed responses, CSRF/auth
  composition where relevant, zero save/queue side effects and redacted errors.

## 2. Generated browser contract and API client

- [ ] Export actual canonical preview/parts routes via inert services without
  network side effects; regenerate `openapi.json` and `schema.d.ts` together.
- [ ] Add small generated aliases and centralized same-origin client functions
  with AbortSignal. Preserve existing `submitVideoBatch` behavior for direct
  inputs; new parts submission uses foundation DTO and refs, not fake URL strings.
- [ ] Ensure an uncertain parts attempt can retain one in-memory key and exact
  payload without automatic retries; add focused request/CSRF/auth/abort/key tests.

## 3. Native import/parts workflow

- [ ] Add explicit favorites-import and exact-part-preview entry points; keep
  ordinary direct BV/share behavior and selector text. Use one native dialog,
  feature-local children and optional reusable collection picker, not nested
  modals or a new global store.
- [ ] Add URL input and distinct bounded preview loading/error/empty/partial
  states, parent page navigation, lazy accessible part expansion and fixed safe
  compatibility/unavailable copy. No automatic selection or eager enumeration.
- [ ] Implement pure stable-key selection helpers, ordered cross-page summary,
  per-part accounting, atomic loaded-scope bulk actions and ≤10 guard. Duplicate
  source occurrences collapse; different CIDs of one BV stay distinct.
- [ ] Show parent + P ordinal + part title and legacy unknown-P1 coexistence
  notice before explicit confirmation. Its initially unchecked acknowledgement
  controls each affected selection's `allow_legacy_overlap`; general submission
  alone never sets it true. Preserve original items; an acknowledged submit may
  add a new independent stable part, without copying old text.
- [ ] Freeze refs/display/destination/key/generation for each attempt and render
  results against that snapshot. Disable duplicate click synchronously, invalidate
  library in background and distinguish queued from completed transcription.
- [ ] Add source change/close/unmount/client-rotation abort/generation cleanup;
  no future request or late result after account/401 boundary. No storage/router
  persistence. Explain accepted work continues when the view closes.
- [ ] Add explicit narrow failure/unknown-result retry UI following foundation
  idempotency and expiry; never blanket-retry successes or silently reselect a
  replacement P after reorder. Use server-supported public-item retry actions.

## 4. Required regression and integration matrix

- [ ] Public sample-shaped eight parent rows preview with zero writes; a list
  page is 20 even though action cap is ten parts. Page 500/10,000-parent cap and
  the foundation's 1,000-part/50-local-page cap are honest, not silent truncation.
- [ ] No selection on expand; single/multi/unavailable/failed parts, repeated
  parents, cross-page/off-page selection, two P2 rows from different BVs and
  atomic bulk over-limit behavior are correct. The 11th part never writes.
- [ ] Optional existing/new/invalid collection tag and note use current limits;
  existing duplicate tags/history remain intact. Legacy unknown-P1 notice plus
  initially-false overlap acknowledgement and explicit new CID import are distinct
  from proven duplicate matching. Missing acknowledgement does not create overlap.
- [ ] Mixed partial quota/queue/source/expiry results map to frozen indices even
  if page state changes. Unknown transport response reuses the same intended
  attempt/key or says uncertainty; changed inputs require a new confirmation.
- [ ] Double-click, held response, source change, closing, remount, abort, 401,
  cookie/account change and cross-tenant refs cannot leak or retarget results.
- [ ] Exact-P input does not become base BV. Reorder/removal resolves or fails
  to the selected stable CID and never another occupant of P. Server-owned
  source/chapter/transcript/chat links preserve part-local timestamp behavior.
- [ ] Existing BV/share parsing/submission, other-platform inputs, SSO and
  chat/history/fixed-viewport behavior remain passing regressions.

## 5. Verification commands and review

Run focused new backend and frontend tests during implementation, then the
full required gates after code settles; no synthetic production services:

```text
.venv/bin/python -m pytest <new favorites parser/transport/service/API tests>
.venv/bin/python -m pytest tests/test_bilibili_url.py tests/test_ingest_submission.py
.venv/bin/python -m pytest
cd web
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm check:api
```

`<new ... tests>` is replaced by actual created test paths in the verification
record. Foundation owns required isolated PostgreSQL migration/part identity
checks; this child records their dependency result and runs relevant integration
regressions, not a production migration. Distinguish existing unrelated failures
from changes and never label skipped provider/canary evidence as passed.

- [ ] Independent Trellis check agent reviews all changed boundaries and fixes
  findings before final verification. Check refs never leak to logs/storage,
  provider data never becomes a fetch destination, and no limits/quotas drifted.
- [ ] Real browser at desktop and 390×844: modal focus/Escape, expand/select,
  count/summary/footer visibility, page scroll/overflow, results, source action,
  demo save gating and clean page-error/console buffers. Preserve user tabs/data;
  fake fixtures are clearly labelled and never replace the live demo silently.
- [ ] A separately authorized bounded read-only public canary records response
  counts/status/limits only; it neither imports the sample nor claims all public
  collections/production egress/subtitles work. No real ingestion without scope.
- [ ] Update actual specs after verified behavior, task verification/artifacts
  and the parent cross-child acceptance record. Do not document plans as live
  conventions. No commit/push/deploy without user authorization.

## Rollback checkpoints

- Before UI wiring, disable/hide the new capability while retaining direct add.
- Before runtime activation, remove only child-owned preview/UI wiring; preserve
  foundation source metadata and saved parts/history. This child has no migration
  or background job to drop. Never reset unrelated dirty files.
- A provider outage disables preview with safe retry feedback; it is not a
  reason to bypass TLS/host/ref/tenant validation or increase limits.


## Implementation gate record — 2026-10-16

The user explicitly approved the complete final plan (“开发”). Foundation
canonical contracts, isolated PostgreSQL races/migration, source identity and
browser projection automated tests, full frontend gates and independent product
review have passed. Native browser QA is unavailable while the Mac is locked;
it remains an unsatisfied combined parent acceptance gate and is not labelled
passed. Dependent development proceeds against the verified source/DTO services
rather than waiting idle for device access. No acceptance criterion is removed.
