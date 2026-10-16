# Research: Multipart Bilibili identity and ingestion design review

- **Query**: Recommend a safe, implementable backend identity/persistence/worker/retry design for independently selected and searchable Bilibili parts, preserving legacy P1 and current limits.
- **Scope**: Internal source review only; no product/schema modifications, task start, network calls, database access or ingestion.
- **Date**: 2026-10-16
- **Confirmed product scope from parent**: Public favorites paged preview/selection plus expand/select multipart videos, with each selected part saved and retrieved independently and linked to its own timeline. Retain the ten-entry selection/batch bound and all configured quotas. An earlier proposal to raise the selection cap to fifty is withdrawn; the existing configured daily-new-item default remains a separate quota.
- **Status**: Architecture recommendation for the parent's design, not an implemented feature. The parent's latest confirmed scope supersedes the older multipart-question wording still present in the current PRD snapshot.

## Recommendation

Use a **server-validated explicit part-reference path** for new multipart saves, with semantic identity **`(bvid, cid)`**, not the ordinal `p`. Keep ordinary `BatchSaveRequest.urls` and its local-only normalizer as the existing P1 compatibility path; do not make local URL normalization perform network discovery or silently reinterpret `?p=2` as P1.

For new explicit part records, a deterministic internal `platform_id` such as `BVxxxxxxxxxx_cid<cid>` can reuse the existing item/dispatch/quota/segment architecture. Include P1 in this explicit stable-CID representation so the new path has one consistent identity rule. The exact spelling is internal and must not be treated as a provider-issued BV or parsed from arbitrary user text.

Persist a small typed source descriptor on `ContentItem` using additive nullable columns, for example `bilibili_bvid`, `bilibili_cid` (validated bounded positive decimal identifier, stored without lossy float conversion), and `bilibili_page` (last verified navigation index). Add constraints for valid shapes/platform ownership and a partial unique key `(user_id, bilibili_bvid, bilibili_cid)` where CID is known. Existing `(user_id, platform, platform_id)` uniqueness remains. A small source-identity JSONB field is technically possible, but typed columns make the invariant/lookup/uniqueness explicit; there is no existing generic metadata column to reuse.

This requires **one additive source-identity migration**, not a durable import job or a new background coordinator. Each selected part remains one ordinary `ContentItem`, dispatch, raw transcript and set of segments, so one selected P counts as one entry against the existing ten-entry and ingestion quotas. Never count a ten-P video as one unit and enqueue ten hidden units behind that count.

## What the current code actually supports

| Evidence | Implication |
| --- | --- |
| `app/models.py:561–627` | `ContentItem` has a text `platform_id`, source URL, metadata fields, raw-artifact pointer and cue-text hash. It has **no cid/source-identity/general-metadata field**. |
| `app/connectors/base.py:26–38` | `ItemMeta` has no source/CID descriptor. A nullable typed connector result extension or a separate typed part-resolution result is needed; existing connector fakes/other platforms keep their defaults. |
| `app/connectors/bilibili.py:135–177` | Admission currently accepts absent `p` or exactly one `p=1`, returns BV/av alone and strips the query from canonical URLs. `canonical_url` rejects synthetic IDs today. |
| `app/connectors/bilibili.py:188–264` | `--no-playlist` remains a safety boundary. Metadata may replace `item.url` with a base BV URL; current projection does not retain or prove CID. The parent additionally verified that the selected yt-dlp metadata output uses an ordinal ID and does not expose authoritative CID. Do not infer CID from that output. |
| `app/ingest/tasks.py:326–340` | The worker currently applies and commits metadata **before** caption extraction. New part checks must precede those writes; merely checking CID after existing commits can already have attached the wrong URL/title to the item. |
| `app/ingest/tasks.py:368–391` | Raw key is tenant/platform/platform-ID/raw-byte-hash, then `raw_format`/pointer are persisted and bytes written. With a safe CID-qualified internal ID, artifact namespaces naturally separate parts. Raw SRT format need not change. |
| `app/ingest/tasks.py:435–447` | Old segments are replaced at the end. Reject source mismatch before raw-object writes, embedding or deleting/replacing any old segments. |
| `app/ingest/submission.py:823` and `app/web/library.py:315` | Channel and browser retry paths requeue the same stored item. Both must retain and enforce its immutable CID pin; neither may rebind it to the current occupant of an ordinal. |
| `app/retrieval/search.py:20–44`, `:94–114` | Retrieval filters exact `platform_id` values and carries `item.url` into citations. Stable part IDs work with the existing per-item segment boundary, but URL/reference scoping needs an explicit mapping to part identities. |
| `app/agent/runtime_state.py:62–74` | Citation validation currently normalizes the citation URL and compares `(platform, platform_id)` exactly. It cannot obtain a CID identity by local `?p=N` parsing; this must be updated using trusted selected item references. |
| `app/browser_capture.py:254–266`; `app/web/transcript.py:74–94` | Bilibili source links currently rebuild query as only `t`; even if `item.url` contains `p`, today's builder drops it. Both transcript blocks and retrieval citations therefore need part-aware source links. |

### Existing fields that must not become a hidden CID store

- `chapters` is JSONB but has actual chapter/timeline semantics and participates in chunking and public chapter DTOs. Putting source identity in it conflates contracts and can break consumers.
- `content_hash` is a hash of normalized cue text, not a source-identity proof; equal subtitles in different parts must still be different sources.
- `raw_object_key` is assigned after successful text extraction and may be absent for a no-caption/failure outcome. Encoding CID only in its path cannot pin the selected source before extraction, and a sidecar/object metadata write is not the authoritative SQL identity transaction.
- `BrowserCapture.capture_metadata` (`models.py:722–756`) belongs to a distinct extension-capture ledger. It is not metadata for ordinary Bilibili connector ingestion and must not be fabricated or repurposed here.
- The connector's in-memory `_meta` cache is per instance and disappears across worker retry/restart. It is useful for consuming the same metadata/caption snapshot, not durable identity.

## Why not use only `BV_pN` plus first-resolved CID?

That option is locally parsable and preserves base BV as P1 with little API disruption. If CID is pinned and mismatch fails closed, it can prevent a retry from silently ingesting a new occupant of P2. It is nevertheless a **position slot with a pin**, not stable content identity:

1. A part moved from P2 to P3 gets a different key and can be duplicated.
2. A different part moved into the existing P2 slot collides with the old pinned item, making correct fresh import impossible without a second identity/escape rule.
3. Persisted `p=2&t=...` links can jump to the new occupant even though retry correctly rejects the mismatch.
4. Pinning only at the first worker fetch is too late when the user selected a preview title/CID earlier; the list can change between preview and first fetch and the worker would pin the wrong content without detecting that change.

The stable-CID explicit path solves the first two issues directly and makes the remaining validation/link obligations visible. It is the recommended internal architecture; no user choice between these key encodings is needed.

## Explicit selection and admission contract

1. Bounded part discovery returns public BV/CID/page/title/duration data plus a short-lived **server-verifiable part reference**. The reference proves the selected BV/CID/navigation snapshot; it grants no authentication or quota bypass.
2. A stateless signed reference can avoid a new preview table: MAC the bounded public descriptor and expiry with a separate purpose key and the current server-resolved tenant as verification context. Do not serialize an internal user ID into the browser payload. A short-lived opaque Redis reference is an alternative if already chosen for preview caching. These are internal choices; raw client CID/page claims alone are not authoritative.
3. The protected import endpoint takes at most ten such references and the existing optional save reason/collection tag, uses canonical Session/Origin/CSRF and idempotency handling, then feeds typed verified references into shared submission admission. Do not tunnel an internal synthetic platform ID through user-supplied `urls` as if it were a public URL.
4. Under existing tenant admission locks, look up a proven `(tenant, BV, CID)` before creating an item. The part index is mutable navigation metadata; it never changes the item's stable identity. Preserve existing duplicate/status/dispatch semantics and per-entry quota/queue results.
5. A direct URL with `?p=N` can go through an explicit bounded “resolve parts” step and produce a verified part ref. Generic local batch admission can stay unchanged and reject unsupported selectors; this does not block the new UI from supporting exact-P input honestly.

Existing public-ID-oriented item management/transcript APIs can keep their identifiers. Optional part display fields can be added to the canonical DTOs if the UI needs them; regenerate types from the same app. CID is public source metadata, not tenant identity, but should still be validated and never confused with an internal item ID.

## Worker ordering and source fence

The first identity pin for an explicit selection is the **server-verified CID committed at admission**, not a fresh guess made after the queue delay. Retry and duplicate worker paths may verify that value, never overwrite it.

Recommended per-attempt sequence:

1. Load/claim the existing dispatch/item and immutable BV/CID; resolve a bounded fresh page list from the fixed public endpoint, outside an open row-lock transaction. Locate the pinned CID and its current page. If absent, fail safely as source unavailable/changed; never fall back to P1 or the old ordinal.
2. Fetch only that chosen page, keeping `--no-playlist`/bounded process I/O. Validate the expected BV/selected-page metadata shape and consume captions from that same connector snapshot. Do not apply `meta.url`'s base-BV fallback to the stored source before proof checks.
3. Re-read the bounded page mapping after metadata/caption acquisition. Require that the selected BV/CID still occupies the page used for the fetch. Missing, moved, duplicate/ambiguous CID, malformed mapping or unverifiable source is a safe failure. A later explicit retry can resolve the same CID at its new index.
4. Only after checks pass, re-lock the item, recheck deletion/purge/dispatch ownership and unchanged pinned source, apply metadata/current-page URL, and continue existing bounded transcript/raw/segment writes. Keep the pin through transient retries, no-caption results and process restart. Never mutate an older record to a different CID to make the retry succeed.
5. Safe `source_changed`/source-unverified errors must be fixed codes in existing failure machinery, without dumping metadata bodies. A mismatch is not a reason to automatically consume five full extraction retries against a different part.

The parent reports no authoritative CID in the current yt-dlp result; a pre/post page-list fence is a required practical check, not a cryptographic proof that the remote platform did not reorder A→B→A during the fetch. If a maintained fixed-CID caption acquisition path is verified, selecting caption data by CID is stronger. Do not invent an unverified endpoint/query or claim provider-side atomic snapshots.

## Legacy P1 compatibility without invented provenance

- Existing `platform_id=BV...` records and raw SRT/segments stay intact, with nullable source fields left NULL. Old public IDs/history/retrieval and ordinary base-URL behavior remain usable. Do not rename IDs or rewrite all old URLs as a migration side effect.
- Current page lists are **not evidence** that a historical P1 transcript came from today's P1 CID. Do not backfill a ready legacy item from current metadata, set its pin during preview, or return a proven-CID duplicate result based only on matching BV/page one.
- If a legacy item already has an independently proven matching source CID, reuse it through the `(tenant,BV,CID)` lookup regardless of its older platform-ID spelling.
- If CID is unknown, a new explicit selected-CID item can be saved separately while retaining the old P1 item. This may leave an honest legacy/current overlap; it is safer than silently merging or relabeling historical text. The UI should distinguish the old unverified source from the new selected part. This is not duplicate known-CID import.
- Ordinary legacy P1 submission stays local-only and retains its BV-based dedupe semantics; it must not claim to dedupe every new CID-qualified part without resolution. If a later **fresh re-ingestion** obtains sufficient source evidence to pin an unverified old row and hits an already-known CID row, stop with a safe source/identity conflict. Do not merge items/segments/history or drop the unique constraint automatically. A future explicit consolidation path can be separate work.

This compatibility exception is a real limitation to document. Keeping `BV` as a global P1 alias while also promising perfect CID dedupe across arbitrary upstream reorderings and unknown historical records cannot be obtained from local URL parsing alone.

## Accurate source jumps

At minimum, part-aware builders must preserve the chosen `p` and add `t` on a fixed `https://www.bilibili.com/video/<BV>` URL; discard unrelated/signed query material. Transcript cues/segments remain part-local, with no artificial concatenated whole-video offset.

However, a stored ordinal URL can become wrong after remote reordering even when ingestion/retry uses stable CID. For a strong “jump to this saved part” guarantee, recommend a small authenticated read-only source resolver keyed by the existing item public ID and bounded timestamp. It verifies item ownership, resolves the pinned CID to a current page with the same bounded public-list service, and redirects only to the fixed Bilibili video origin with `p`/`t`. Missing/unverifiable CID produces a safe unavailable result, not a wrong-part redirect. Legacy unpinned records retain their existing link behavior and honest provenance limitation.

This resolver needs no job/migration beyond source fields, but source-link DTO/citation construction must deliberately support it. Generate trusted item-public-ID links on the server, do not let an LLM invent resolver URLs or normalize them as arbitrary external input. Exact-reference scoping should use the server-selected part/item identity (or resolve an already-known owned part mapping), rather than assuming a local `?p=N` string can prove CID. Do not add live provider calls to every retrieval hit merely to render a citation; resolve on a user's actual source-open action if current remapping is required.

## Required implementation evidence for the parent plan

- Legacy rows survive the additive migration with NULL source identity; no network backfill. Proven `(tenant,BV,CID)` uniqueness and concurrent selection dedupe are tested on isolated PostgreSQL.
- One part, reordered parts, removed CID, duplicate/malformed pages, stale preview versus worker, pre/post mapping change and metadata base-URL fallback all fail or resolve without cross-part attribution.
- Retry after extraction/embedding/publication failure preserves the pin; source mismatch cannot overwrite old metadata/raw pointers/segments. Raw formats/byte limits and tenant object prefixes remain compatible.
- Two parts with identical subtitle bytes/text remain separately retrievable and correctly attributed; titles/duration reflect the selected part. No all-parts expansion hides behind one quota entry.
- Part source links retain `p`/`t`, and a current-CID resolver cannot redirect to arbitrary hosts or leak another tenant's item. Citation exact-reference checks use the correct stable part identity.
- Public preview/part refs are authenticated, bounded, tamper/expiry/account-switch checked, have no save side effects, and do not carry Bilibili credentials. The user-selected ten cap and all existing quotas remain unchanged.

No product code, schema or tests were changed or executed for this review. External protocol details and whether CID is stable across all provider editing cases remain subject to the parent's maintained-source/canary evidence; a disappeared or changed CID must be handled as unavailable rather than guessed.
