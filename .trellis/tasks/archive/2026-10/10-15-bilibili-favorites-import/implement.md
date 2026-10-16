# Execution Plan

Execution update: final approval was received with “开发” on 2026-10-16.
Both children were implemented and independently reviewed in dependency order;
parent integration/evidence is in verification.md. Root IAB acceptance followed
earlier unavailable-device attempts. The later commit-plan approval (“继续”)
produced b4d60fe, 2bb6dbc and 788b2ae after independent actual-index gates.
Product work is committed; archive/journal bookkeeping is the remaining step.

The sections below preserve the original execution plan; its planning gate was
fulfilled before implementation. Completion evidence is in verification.md.

## Ordered Work

1. Read parent/child artifacts and curated contexts; if context injection warns
   that a spec is truncated, read the full source file before editing/checking.
   In particular, agent-retrieval-convergence.md exceeds the injection file cap.
   Snapshot task-overlapping WIP without changing SSO/chat/BV work.
2. After approval, start 10-16-bilibili-multipart. Dispatch Trellis implement,
   then independent check. Complete typed identity/additive migration, part
   discovery/ref/import contracts, worker fence and browser source navigation.
3. Only after foundation gates pass, start 10-16-bilibili-favorites-preview.
   Implement bounded collection discovery and preview/selection/results UI;
   generate contracts and run an independent check.
4. Parent integration: favorites -> expand -> select P2/P3 -> separate ingestion
   fixtures -> retrieval -> current/history/transcript/chapter source jumps.
   Verify reordered/removed sources, legacy warning and cross-account teardown.
5. Update affected specs/docs, record test evidence and real readiness limits,
   prepare scoped diff/commit plan. No implicit commit/push/deployment or real
   sample import; production migration needs separate authorization.

## Validation

- Strict parser/transport tests: fixed hosts, redirects, bounded decoded data,
  total deadline/streamed bytes, page/part caps, provider safe-error matrix and
  no preview persistence/queue effects.
- Session/ref/API tests: Origin/CSRF, auth/ownership, tamper/expiry/wrong-account,
  ten-item bound, stable unknown replay, frozen indices, partial quota/queue
  failures, save-disabled demo and unchanged ordinary BV/share/YouTube paths.
- Isolated PostgreSQL: one Alembic head and upgrade/current/check, legacy NULL
  provenance, concurrent known-CID dedupe, source pin preservation and no
  metadata/raw/segment replacement on mismatch/deletion/retry races.
- Worker/retrieval: independent P2/P3, equal-text attribution, no-playlist,
  actual extractor-selected CID proof for metadata/subtitle/no-caption paths,
  cached-HTML versus fresh-map disagreement, before/after mapping guards and all
  source/exact-reference consumers. Prove subtitle extraction is enabled without
  media/file download; missing CID proof is never treated as success.
- Browser: loaded-only bulk select, cross-page parts, exact-P input, eleventh
  blocked, legacy-overlap consent, partial/unknown results, double-click,
  close/unmount/401/client rotation, desktop and 390x844 focus/scroll/Escape,
  clean console and no persistent private browser data.
- After implementation run focused pytest, then full .venv/bin/pytest -q;
  pnpm --dir web test/typecheck/lint/build/check:api, .venv/bin/alembic heads,
  git diff --check. Child plans contain exact focused commands.
- Prior full-suite baseline had two date-dependent recycle-bin assertions in
  tests/test_item_management_tools.py (August seeds vs October execution).
  Reverify/report independently; do not edit unrelated tests to hide failures.
- Read-only real metadata canary may verify provider fields/part identities;
  never import the user's eight collection entries or start shared/production
  DB/Beat to test. Stop isolated temporary services when checks finish.

## Review and Rollback Gates

Before start, require PRD/design/implement plus real implement/check manifests
for parent and both children and explicit final approval. Check every R/AC
mapping during parent review. Preserve ten and all existing config quotas.
If source identity cannot be verified, report the concrete gap and keep old
content; never silently reduce multipart behavior to P1 or ordinal-only IDs.
Rollback task-scoped edits only, retain additive data and use part-aware workers.
No production migration at build/import/request time or from planning/tests.

## Present Evidence

Research and the successful public-list read are under research/. All tasks
remain planning. Independent final planning review PASS is recorded in research/planning-review.md;
all parent/child artifacts and manifests validate. No product code, source migration,
real import or deployment has occurred; planning checks do not count as implementation
verification.
