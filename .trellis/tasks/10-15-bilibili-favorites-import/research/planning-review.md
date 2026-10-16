# Independent planning review

Reviewed 2026-10-16. Planning artifacts only; no product files, migration/start,
database, queue, real import or remote provider requests were used in this review.

## Review status

**PASS — ready for the user's final plan review.** No unresolved blocking
planning findings remain after reading the revised parent and both child
artifacts. This is not implementation approval, implementation completion, a
live-provider qualification or deployment readiness. All three task statuses
remain `planning`.

Initial review found one source-proof blocker and two cross-artifact alignment
issues; all are resolved in the current plans as recorded below. Existing
product/WIP was not changed. Current context manifests contain real spec/research
entries rather than seed examples; the parent and foundation execution plans
require full source-file reading when injected specs are truncated.

The final user scope is public favorites plus independent multi-P handling,
with ten selected parts maximum and all existing quotas unchanged. Separate
parts, explicit preview/selection, typed additive source storage, signed tenant-
bound refs, safe legacy coexistence and current/history source opening fit that
scope. No new product decision is needed to resolve the technical proof issue.

## Findings and resolution

1. **Actual extraction CID proof (blocking until revised):** The initial parent
   and multipart child use equal pre/post pagelist mappings plus BV/P shape as
   extraction identity proof. Installed yt-dlp selects actual CID from webpage
   initial-state `videoData.pages`, not the external before/after pagelist reads.
   Different cached snapshots can already disagree; an A→B→A race is not the only
   issue. Require actual subtitle-extraction CID evidence equal to the immutable
   admission pin before any metadata/raw/segment write. Missing/mismatched proof
   must fail closed. Pre/post mapping remains supplementary evidence only.
   **Resolved:** parent and foundation now require actual extraction-CID evidence,
   including no-caption outcomes, and specify a controlled isolated adapter with
   enabled subtitle extraction. Unchanged map responses cannot substitute for
   this evidence. Implementation still must test the adapter/hook and bounds.
2. **Collection cap/aliases:** Parent initially specifies pages 1..500/detail
   alias; favorites child initially specifies 1..1000 plus play alias. The owners
   are converging on 500 and the existing detail alias. Synchronize all DTO,
   transport, AC and execution-plan statements before declaring readiness.
   **Resolved:** collection pages now cap at 500/10,000 parent entries and accept
   the space/favlist plus medialist/detail/ml forms. The separate part-descriptor
   cap remains 1,000 parts/50 local pages, consistently distinguished from the
   collection bound and the unchanged ten-entry action limit.
3. **Submission provider reads:** Favorites text initially implies re-fetching
   current stable identity during HTTP admission; foundation explicitly admits
   signed pins without a provider fetch and verifies current content in the
   worker. State this distinction consistently. A valid signed descriptor is
   verified source-selection evidence, not proof of current availability.
   **Resolved:** favorites design now explicitly verifies refs locally at
   admission and delegates fresh current-source plus actual-CID checks to the
   worker. There is no ten-descriptor synchronous provider loop in admission.

## Feasible actual-CID adapter: local dependency experiment

Read the installed yt-dlp 2026.08.19 implementation, including:

- `BiliBiliIE._real_extract`: selects CID, then invokes `_download_playinfo`,
  `_get_chapters` and `extract_subtitles` using that local CID.
- `InfoExtractor.extract_subtitles`: calls `_get_subtitles` only when
  `writesubtitles` or `listsubtitles` is enabled. The current StashSeek CLI does
  not enable either, so simply copying its arguments would not establish this
  hook or real caption behavior.
- `BilibiliBaseIE._get_subtitles(video_id,cid,aid=None)`: obtains CID-specific
  caption metadata and returns inline SRT `data` for available language tracks.
- `YoutubeDL.add_info_extractor` and `extract_info(...,download=False,
  process=False,ie_key=...)`: allow a feature-specific subclass to use the real
  extractor while retaining control over projected output and downloads.

An in-memory offline experiment registered a small subclass, retained the real
`_real_extract` and base `_get_subtitles`, and stubbed all webpage/JSON/play-info
I/O to fixed fixtures. Any unexpected request raised an assertion. It explicitly
enabled `writesubtitles=True`, `skip_download=True`, `noplaylist=True`, called
`extract_info(download=False,process=False,ie_key=<fixture subclass>)`, and
intercepted `_get_subtitles(video_id,cid)` to compare the actual values with the
expected source. No fixture was saved into product code and no network was used.

| Fixture | Observed result |
| --- | --- |
| P2, expected CID 202, available caption | Actual play-info and subtitle calls used BV + CID 202; result ID ended `_p2`; returned inline SRT with local 0–2 second cue. |
| P2, expected CID 202, no caption | The actual subtitle hook still received and verified CID 202; only danmaku remained, correctly distinguishing identity proof from caption availability. |
| P2, expected CID 999 | Hook observed CID 202 and raised safe `source_changed`; no success/proof event or accepted caption returned. |

This demonstrates a viable dependency integration point, not a production
implementation or live-provider compatibility test. A controlled adapter can
emit a small typed proof alongside projected metadata/inline caption text and
reject absent/multiple/mismatched BV/CID observations. It must pin the actual
subtitle call, including the no-caption path; observing a separate pagelist or
play-info request alone is insufficient to prove which caption data was used.
Validate the part-title source against the same CID-aware descriptor rather than
trusting a title inferred from a potentially different ordinal snapshot.

Implementation must still verify subprocess wall deadline, output/body limits,
no media writes, no credentials/config/cookie adoption, redacted errors and
unsupported playlist/interactive/fragment cases. Registering a subclass should
not enable unrelated default extractors/redirect fallback. Tests must fail when
the dependency hook changes or extraction returns no verifiable CID. This is a
technical implementation gate, not a reason to weaken the stated source fence.

## Positive review coverage

- R/AC matrices include public-list access versus transcript availability,
  independent P-local transcripts, stable dedupe and identical-text attribution.
- Legacy rows remain NULL/unverified; default-false explicit coexistence consent
  does not infer historical CID, copy text, replace IDs or merge old history.
- Browser source projection covers detail/transcript/chapters/current JSON/SSE
  and reopened history using tenant-owned public item IDs and bounded lookups.
  Generic MCP/channel URLs stay provider-direct; model URLs grant no authority.
- Explicit P input is routed through part preview instead of being silently
  changed to P1. Existing ordinary local BV/share behavior is preserved.
- Unknown-result replay, expiry, frozen indices, partial quota/queue outcomes,
  account teardown, no storage persistence and no unbounded import loops are
  included. Accepted siblings survive a failed entry or closed modal.
- Additive migration, one Alembic head, isolated PostgreSQL and part-aware worker
  rollback preserve existing data and unrelated SSO/chat/BV WIP.

## Final readiness checklist

- [x] Read revised source-proof contracts in parent and multipart child.
- [x] Read synchronized page-cap/alias/admission semantics in favorites child.
- [x] Validate parent and both children have PRD/design/execution artifacts and
  real contexts. Parent implement/check each have 15 existing spec/research
  paths, foundation each 14, favorites preview each 15; no examples, missing
  paths or product-code entries were found.
- [x] Confirm full-file fallback for oversized injected specs in the parent and
  foundation plans; it applies before editing or checking affected boundaries.
- [x] Confirm all three tasks remain planning and record final pass without
  activating a task or touching product code.

Implementation evidence remains outstanding by design: migrations/isolated
PostgreSQL races, actual worker extraction proof, provider transport bounds,
source-opening browser behavior and all listed regression gates. The local
dependency fixture establishes feasibility only. No further product choice or
outside access is required to submit these plans for final user review.
