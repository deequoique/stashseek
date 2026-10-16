# B站分P独立存储与来源定位

## Goal and authorization

Preview a Bilibili video's parts, explicitly select them, save each as an
independent searchable item, and open its correct source part at the cited time.
This child provides shared primitives before the public-favorites-preview child.

The user confirmed favorites import plus multipart support and retained the ten
entry limit and all quotas. Planning is authorized; implementation/task start,
production migrations and deployment await the parent's final plan approval.
Preserve all existing SSO/BV/chat WIP and read-only demo restrictions.

## Requirements

- M1: Protected, bounded read-only preview accepts an actual BV/video URL,
  including explicit `p=N`, and shows part titles/indices without saving/queuing.
- M2: New explicit selections identify stable `(BV,CID)` content. Each selected
  P is one entry/item/dispatch with its own lifecycle, transcript, retrieval scope
  and public item ID, subject to the existing ten cap and all configured quotas.
- M3: Server-verifiable part refs expire after 30 minutes, bind to the current
  tenant through verification context and expose no internal tenant ID.
- M4: Ordinary BV/share-text batch admission remains local-only P1 compatibility.
  Old rows/history remain intact; missing historical CID is never inferred from
  today's page map or silently backfilled.
- M5: Known identical tenant/BV/CID deduplicates. Unknown legacy overlap is
  rejected by default; UI warns that a verified part will be separately saved,
  offers cancel/skip and requires per-entry consent before creation. No merge,
  overwrite or relabel of historical sources.
- M6: Require CID evidence from the actual controlled subtitle/media metadata
  extraction snapshot and equality to the pin; pre/post page maps are additional
  guards, never substitute proof. Reorder/cache mismatch/missing proof, including
  no-caption and retry/restart, cannot attach another P's metadata/raw/segments.
  Keep no-playlist, metadata-only/no-file behavior and extraction/embedding bounds.
- M7: Browser detail/chapters/transcript/current JSON/streamed citations/reopened
  history use server-owned source navigation for verified parts. Opening resolves
  the owned CID to current P and redirects only to fixed Bilibili with local time.
- M8: Generic Agent/MCP/channel citations retain provider-direct URLs. Browser
  resolver paths and LLM-authored ordinals never authorize trusted source scope.
- M9: Fixed-host public metadata uses verified TLS, no credentials/redirects,
  15-second whole-call and 1 MiB limits, at most 1000 parts and local pages of 20.
  Preview/source opening share Redis tenant 30/min and global upstream concurrency
  at most two; gate failure fails closed, without new background jobs.
- M10: Reuse canonical Session/Origin/CSRF, generated DTOs, save-enabled,
  idempotency, quotas, partial results and bounded publish/worker admission.

## Acceptance criteria

- [ ] AC1: Preview P1/P2/multiple parts without writes/queue effects; malformed,
  private, missing, oversized and provider/gate failures are safe truthful errors.
- [ ] AC2: Tampered/expired/other-tenant refs and >10 arrays cannot create items;
  valid peers preserve order, stable key replay and ordinary partial outcomes.
- [ ] AC3: One additive migration preserves NULL legacy sources and one Alembic
  head. Isolated PostgreSQL proves typed constraints and concurrent known-CID dedupe.
- [ ] AC4: Unknown legacy overlap is rejected unless explicitly consented, then
  stored separately; proven legacy CID reuses its existing item. History is intact.
- [ ] AC5: Stale preview, removed/reordered CID, cached extractor-page mismatch,
  missing actual CID and retry cannot replace the pin or alter old metadata/raw/
  segments. No-caption also proves the actual selected CID; no files/media download.
- [ ] AC6: Identical subtitle bytes in different parts remain separate retrievable
  sources; source jumps retain local time and find current P after reorder.
- [ ] AC7: Deleted/missing/foreign-owned source safely fails without wrong-host/P
  redirect. Every browser current/history/transcript/detail projection is covered;
  generic MCP remains provider-direct and does not trust model resolver paths.
- [ ] AC8: Relevant legacy BV/share/YouTube, retry/quota/account regressions,
  canonical generated contracts, frontend gates and real-browser picker/source
  flows pass before completion; demo writes stay disabled.

## Dependency and exclusions

This foundation owns part preview/signing/import, source schema/worker fences,
reusable part picker/direct-P entry and complete browser source projection.
`10-16-bilibili-favorites-preview` depends on these contracts and must reuse them.
No private Bilibili login/cookies, hidden playlist expansion, automatic whole-folder
job, quota increase, legacy consolidation or deployment is included.
