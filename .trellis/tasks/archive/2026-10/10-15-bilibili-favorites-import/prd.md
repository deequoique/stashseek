# Bilibili Favorites Import and Multipart Videos

Execution update: the complete plan was explicitly approved with “开发” on
2026-10-16. Local implementation, independent review and combined verification
are recorded in verification.md. The original planning/baseline statements below
describe the starting state. Production deployment/migration and real imports
remain outside authorization; Git work commits await the workflow confirmation.

## Goal

Preview public Bilibili favorites, expand videos into selectable parts, and save
selected parts as independent searchable sources with accurate browser jumps.

## Confirmed Background

- Public-favorites task creation/planning was approved on 2026-10-15. On
  2026-10-16 the user chose preview/selection, added multipart handling, and
  finalized two deliverables: favorites import and multipart handling. The
  suggested increase to 50 was withdrawn: keep the existing ten-entry bound and
  every configured ingestion quota. Each selected P counts as one entry.
- The supplied `https://space.bilibili.com/400561576/favlist?fid=4157547776&ftype=create`
  returned public title `study`, eight entries, `has_more=false`, without login.
  This verifies list access only; eight videos may contain many more parts.
  Evidence: `research/public-sample-access.md`.
- Current cap is ten (`app/ingest/submission.py:33,480`); active/user default is
  ten and daily new/user default fifty (`app/config.py:305,308`). These are
  StashSeek policies, not Bilibili collection-size limits.
- Admission currently accepts absent p or p=1 only
  (`app/connectors/bilibili.py:151`), uses --no-playlist (`:192`), and stores
  BV-only identity (`app/models.py:575,625`). Timestamp builders discard p
  (`app/browser_capture.py:254`, `web/src/videos/VideoDetailView.tsx:32`). Simply
  admitting p=2 would not provide independent identity or accurate navigation.
- Existing SSO, chat/history and BV/share-input WIP must be preserved. Read-only
  demo capabilities remain authoritative. Scope approval does not authorize
  production migration/deployment or importing the user's real collection.

## Requirements

- **R1 Public discovery:** Accept public space/favlist and medialist/detail/ml
  links, including the sample. Read without Bilibili credentials. Distinguish
  invalid/private/unavailable, empty, rate-limited and temporary failure states.
- **R2 Preview and selection:** Preview never saves/enqueues. Page through the
  collection, explicitly expand videos and choose parts. Default to none;
  bulk selection means displayed eligible parts, never unseen whole collections.
- **R3 Shared ingestion:** Reuse tenant ownership, dedupe, idempotency, lifecycle,
  queues and all quotas. One P is one item/slot; reject an eleventh selection.
- **R4 Honest outcomes:** Preserve earlier content/history. Show per-item
  accepted/duplicate/failure results and truthful partial/unknown outcomes.
  No hidden loops, blanket retries or rollback of accepted siblings.
- **R5 Bounded reads:** Bound time, bytes, counts, pages and request frequency;
  fetch only fixed allowed upstream hosts. Show reached bounds, never silently
  report truncated data as a complete collection or an error as an empty list.
- **R6 Subtitle truth:** A listed part does not promise captions. Preserve
  per-part needs-action/failure states; no claim of a new ASR/Bilibili extension.
- **R7 Independent parts:** Each P has its own source identity, title, text,
  local time axis, state, retry and attribution. Known identical part selection
  dedupes; different parts with identical text remain independently attributable.
- **R8 Browser navigation:** Part transcript/chapter/answer source actions open
  the selected content at its local time. Upstream reordering resolves to its
  current P; removed/unverified sources fail safely instead of opening P1.
- **R9 Source fence:** Preview-to-worker changes and retries cannot silently
  bind another part or overwrite old metadata/transcripts/source provenance.
- **R10 Legacy compatibility:** Preserve ordinary BV/share behavior and all old
  IDs/text/history. Unknown old P1 provenance stays unknown. Before importing a
  verified part overlapping an unverified legacy P1, explain the possible new
  record and require explicit overlap acceptance; no automatic merge/backfill
  or copied/relabelled historical text.
- **R11 Exact-P input:** The part-preview workflow accepts a video ?p=N link and
  resolves that part explicitly. Do not strip p and substitute P1. Bare BV keeps
  ordinary direct-add P1 meaning, without silently expanding every P.

## Acceptance Criteria

| ID | Observable outcome | Requirement |
| --- | --- | --- |
| AC1 | Sample-shaped public list has title and paging; preview makes no item/dispatch. Empty and failure differ. | R1,R2,R5 |
| AC2 | Multi-P fixture expands; P2/P3 save independently without unseen siblings. Exact-P input selects its actual part. | R2,R7,R11 |
| AC3 | Selections survive loaded-page navigation; known duplicates dedupe; an eleventh is blocked; cap/quota defaults do not change. | R2,R3,R7 |
| AC4 | P2/P3 text/time axes and equal-text parts remain independently retrievable and attributed. | R7 |
| AC5 | Browser source at t=42 opens correct P/content, including after reordering. Missing CID never redirects to another P. | R8,R9 |
| AC6 | Changed source, retry, duplicate delivery or deletion cannot attach another P's data or hidden sibling work. | R3,R7,R9 |
| AC7 | Mixed queued/duplicate/quota/queue errors stay visible; unknown response is unknown; queued never means captions ready. | R4,R6 |
| AC8 | Legacy rows/history/WIP survive. No guessed CID migration; possible legacy overlap requires explicit confirmation before write. | R10 |
| AC9 | Anonymous/CSRF/foreign-item, forged/expired/cross-account references fail closed; account rotation clears private state. | R3,R5 |
| AC10 | Direct BV/share/YouTube behavior and demo save-disabled behavior remain intact. | R10,R11 |

## Out of Scope

Private favorites, Bilibili credentials, synchronization, automatic unseen/all-P
import, durable whole-import pause/resume jobs, higher caps/quotas, automatic
legacy merging/conversion, new extension/ASR/media downloads, unrelated SSO/UI
changes, production migration/deployment and actual import of the user's sample.

## Delivery Map and Readiness

1. `../10-16-bilibili-multipart`: part identity/discovery/admission, isolated
   ingestion, legacy compatibility and browser source navigation.
2. `../10-16-bilibili-favorites-preview`: collection discovery, part selection,
   confirmation/results UI and integration. Depends on verified child 1.

The parent owns these source requirements and combined acceptance. No unresolved
product choice remains. All three tasks stay planning pending complete-artifact
review and a subsequent explicit implementation approval.
