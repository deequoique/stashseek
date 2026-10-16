# Bilibili multipart ingestion gap

Research date: 2026-10-16. Planning only; no product changes, database access,
queue publication, video import or media fetch was performed.

## Scope state supplied by the parent agent

The user has now included multipart expansion/selection, independent saving and
retrieval, and accurate part-specific timestamp links. The user retained the
existing ten-entry request limit and all existing quotas. This record provides
technical options; it does not choose an identity/migration policy for the user
or authorize implementation.

## Verified current behavior

| Boundary | Evidence | Current result |
| --- | --- | --- |
| Local URL admission | `app/connectors/bilibili.py:136-171`, `tests/test_bilibili_url.py:202-218` | Missing `p` or exactly one `p=1` is accepted. Other/multiple/empty values fail. Canonical identity is the BV/av only. |
| Fetch selection | `app/connectors/bilibili.py:182-199`; installed yt-dlp 2026.08.19 `BiliBiliIE._real_extract`, lines 767-790 | Connector passes the base URL plus `--no-playlist`. yt-dlp reads page metadata; when there are multiple pages, it chooses part 1 unless an explicit part is supplied. |
| Upstream per-part content | Same installed extractor lines 785-790, 816, 847-897 | yt-dlp selects a part's `cid`, suffixes its result ID with `_pN`, and fetches subtitles/chapters for that CID. This is an implementation capability of the dependency, not current StashSeek support. |
| Stored identity | `app/models.py:562-627`; `app/ingest/submission.py:633,724` | Unique `(user_id, platform, platform_id)`; the item has one source URL and one transcript. BV-only identity cannot distinguish P1/P2. |
| Stored locator | `app/models.py:959-988` | A segment has item ID and local start/end seconds, with no part/CID field. Independent items could retain these local times; an aggregate item would require extra locator semantics. |
| Metadata persistence | `app/models.py:577-606`, `app/connectors/base.py:22-35`, `app/ingest/tasks.py:326-339` | There is no general source-metadata/CID field. `chapters` and `tags` have existing public meanings; embedding CID/identity state there would conflate contracts. Worker replaces these fields with connector metadata. |
| Backend citation link | `app/browser_capture.py:254-267`; `app/retrieval/search.py:20-42` | Bilibili links discard all source queries and emit only `t`. Simply preserving a P2 source URL elsewhere does not fix citations. |
| Frontend transcript link | `web/src/videos/VideoDetailView.tsx:32-37` | Also discards Bilibili queries, so a P2 timestamp would open P1. |
| Extension | `extension/src/protocol.ts:2`, `extension/src/page-capture.ts:85-88`, `app/browser_capture.py:23`, `extension/manifest.json` | Only YouTube/NTU capture paths exist; there is no Bilibili adapter, CID capture, or Bilibili host permission. Do not promise the current extension handles a missing Bilibili part transcript. |
| Admission/work budgets | `app/ingest/submission.py:33,480`, `app/config.py:304-332` | Batch maximum 10; default active/user 10, daily new/user 50, storage/user 1000, plus global and daily-dispatch gates. Dependency multipart support does not remove these safeguards. |

Repository Bilibili spec explicitly documents P1-only behavior and requires local
preflight with zero provider requests. The new design must intentionally replace
that restriction and update all affected contracts, not strip `p` before checks.

The maintained upstream source was also opened read-only:
https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py
The installed version is the precise runtime evidence above; master can drift.
No actual multi-P provider canary or browser link behavior was verified here.

## Minimum coherent product model

Recommend one saved item and one ingestion dispatch per selected part. Show
the parent title plus `P<number> <part title>`, retain the BV grouping in preview,
and explicitly show the number of selected parts. A 30-part course can therefore
be expanded and selected in successive actions of at most ten entries, subject
to existing active/daily quotas. This accounting is a recommendation implied by
independent work, and must be made explicit in the final scope summary.

Do not silently import every part when the user selects one parent row. A parent
checkbox may select only currently loaded eligible parts up to the action limit,
or request a deliberate expansion first. Part discovery needs its own bounded
response/time/count limits; a playlist flag does not make discovery bounded.

Unavailable subtitles remain a per-part `needs_asr` / `needs_extension` / failure
outcome. Successful favorites or part discovery is not a promise of transcript
availability. One failed part must not erase successful siblings.

Aggregating all P subtitles under one BV is a larger alternative: add a durable
part identity and segment locator, per-part status/retry/object ownership,
part-aware rendering/search/citations, and explicit work accounting. Concatenating
subtitle times or titles without those fields creates ambiguous timestamps and
cannot meet accurate part-specific navigation. It is not the minimum first cut.

## Identity options and compatibility

### A. Ordinal identity with legacy P1 compatibility

Use existing `BV...` for missing `p`/P1 and a strict `BV..._pN` identity for P2+.
The current text column and unique key can represent this without a DDL change.
Admission stays purely local and canonical source URLs retain only valid `p=N`.
Connector parsing/building must understand the composite ID, rather than send
the composite as the public BV or discard the part query. All filtering,
retrieval matching, key paths, Agent reference parsing, retries and timestamp
builders must use the same reference contract.

This prevents ordinary P1/P2 collisions but ordinal alone is not a stable content
identity. If the uploader rearranges/deletes parts, P2 can refer to different
content. Storing an initial CID and verifying it at ingestion/retry detects this
change before indexing; it does not by itself stop an old `?p=2&t=...` citation
opening different content after a later reorder.

There is currently no suitable dedicated CID field. Encoding ordinal plus CID
into the platform ID avoids DDL for new P2+ items but changes the identity when
the ordinal changes and cannot attach a CID to the old BV-only P1 identity.
Using tags/chapters/description as hidden identity storage is not recommended.
A new nullable typed source metadata field/table is cleaner if choosing this
policy. Therefore do not promise both durable CID binding and complete legacy
P1 compatibility without a schema/identity transition plan.

### B. Stable `(BV, CID)` part identity plus structured submission

Recommended for the stated requirement of accurate part-specific references.
Public preview returns validated BV, CID, observed P number and title. Submission
revalidates those fields against a bounded trusted descriptor before admitting
an item; caller-supplied CID/title is never trusted as provider proof. Selection
of the same CID through separate collection pages or changed part order dedupes.

The existing text `platform_id` could encode a strict `BV..._cid<digits>` for new
items, so a brand-new part identity does not inherently require DDL. However,
preserving existing BV-only P1 while persisting its CID/ordinal association needs
either an explicit legacy adoption/migration rule, a dedicated metadata field,
or a side mapping table. There is no existing general-purpose metadata column.
Adding source metadata with one forward-compatible migration is preferable to
implicit hidden state when legacy identity must be retained.

For direct `?p=N` URLs, local preflight should parse a symbolic BV+ordinal only;
it cannot produce a CID without network. Resolve that symbol later in the
side-effecting submission service, outside database locks and with a bounded
provider request, then apply stable part identity and quota/idempotency checks.
The implementation must distinguish a local parsed request from a resolved
authoritative ItemReference, instead of making the existing zero-network
normalizer fetch metadata. Bare BV keeps its explicitly documented P1 meaning;
it must not suddenly fan out to every part.

Stable storage does not automatically give a stable public Bilibili link: no
evidence here establishes that a `cid=` query pins the Bilibili web player.
Do not invent such a guarantee. To withstand later reordering, a StashSeek
source-opening route can re-resolve stored CID to the current ordinal, then
redirect only to a fixed validated Bilibili `?p=N&t=...` URL. Missing/changed CID
must show a safe unavailable/stale-source outcome instead of opening a different
part. This adds a bounded provider operation on source opening and must be
reviewed as part of the navigation contract. Alternatively, retain direct links
with an explicit limitation about later upstream changes; that offers weaker
accuracy than the full stated requirement.

### Recommended next design decision

Decide whether accuracy includes surviving uploader reorder after ingestion.
If yes, adopt stable BV+CID identity, explicit legacy P1 association and dynamic
part-aware source opening; allow a schema migration when needed. If the first
release only guarantees the ingested snapshot and requires refresh upon stale
ordinal, ordinal IDs are smaller but that limitation must be explicit. Neither
option should silently equate `BV`, `BV?p=2` or two different CIDs.

## Required verification for whichever option is chosen

- P1 backward compatibility, P2/P3 independent admission/storage/text, repeat
  selection dedupe, cross-tenant isolation, retry and deletion of one sibling.
- Strict positive bounded integer `p`, duplicate `p`, tracking/fragment guards,
  av/BV normalization, malformed IDs, and zero-network local preflight.
- Preview-to-submit CID mismatch/reorder/deletion, ambiguous metadata, and
  bounded provider failures without admission or quota consumption.
- Part-local subtitle/chapter timings, per-part raw key ownership and titles.
- Backend citations and frontend transcript/detail links retain correct part
  and seconds; an upstream reorder never silently opens a different part if
  stable navigation is selected.
- Ten selected parts maximum, unchanged active/daily/storage/dispatch limits,
  truthful partial results, no queue bypass or unbounded hidden expansion.
- No Bilibili credential collection and no claim that the existing browser
  extension is already capable of capturing Bilibili subtitles.
