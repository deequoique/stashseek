# Multipart foundation design

## Status and architecture

Planning only; no task activation or product changes. This is the canonical
multipart contract referenced by the parent's overall design. Implement this
foundation before `10-16-bilibili-favorites-preview`, which reuses its services,
picker, refs, gates and import/source routes.

## Source schema and compatibility

Add nullable typed `ContentItem.bilibili_bvid`, `bilibili_cid`, `bilibili_page`.
Use strict normalized BV spelling, a bounded canonical positive decimal CID string
(no float conversion), and positive current navigation index. Validate all-three-
NULL legacy rows versus complete Bilibili descriptors. Add partial unique
`(user_id,bilibili_bvid,bilibili_cid)` where CID is known; retain existing
tenant/platform/platform-ID uniqueness. One additive migration follows the current
Alembic head, without network backfill or historical rewrite.

New explicit selections, including P1, use internal `platform_id=<BV>_cid<CID>`.
These are server-owned keys, not synthetic strings admitted from user URLs.
Known-CID lookup happens before creation under existing admission locks. Current P
can change without changing CID/public item/transcript identity. Do not misuse
chapters, cue content hashes, object keys or BrowserCapture metadata as the pin;
extend the typed internal source/connector result with nullable defaults instead.

Old plain-BV rows keep NULL CID, public IDs, URLs, transcripts and history. Ordinary
local batch retains its P1 semantics and does not claim CID-aware dedupe without
proof. A proven matching legacy source is reused. An unknown legacy source is not
proved by today's P1 map: preview warns of a separate verified record and each
selection defaults to `allow_legacy_overlap=false`. Without true consent, return
`legacy_identity_unknown` without writes. UI offers cancel/skip and a default-off
per-entry checkbox; general import confirmation is not a substitute. With consent,
create the separate CID item and leave history untouched. If a new overlap appears
after preview, default-false import still rejects it. A later fresh legacy ingest
that conflicts with a known CID safely fails; never auto-merge historical rows.

## Canonical routes and DTOs

Mount through existing `app/api/app.py`/`runtime.py`, ordinary Session/Origin/CSRF,
safe error envelope, request bounds and `no-store`. Both POST routes are protected;
preview remains side-effect-free. Import alone uses save-enabled and idempotency.

### `POST /api/v1/bilibili/parts:preview`

`BilibiliPartsPreviewRequest`: `url:string(1..2048)`, optional `page:int(1..50)`.
Accept a complete BV or strict HTTPS Bilibili video URL with absent/exactly one
positive bounded `p`. Reject credentials, ports, fragments, duplicate selectors,
malformed paths/hosts, collection URLs and arbitrary prose here. If `page` is
omitted, choose the local page containing an explicit requested P, otherwise page
one; an explicit `page` controls subsequent navigation. No selection is automatic.
Generic `BatchSaveRequest.urls` is unchanged/local-only and need not accept P2.

`BilibiliPartsPreviewResponse`:

```text
bvid, title:string|null
page:int, page_size:20, total_parts:int(0..1000), has_more:bool
requested_page:int|null             # P from input URL, not local page number
parts:BilibiliPartPreview[]          # at most 20

BilibiliPartPreview:
  part_key:string                  # stable public BV:CID, UI dedupe only
  part_ref:string|null             # signed opaque proof, absent if unselectable
  page:positive int                # current P index
  title:string|null, duration_sec:int|null
  selectable:bool, unavailable_reason:closed safe enum|null
  has_unverified_legacy_item:bool
```

All strings/counts/IDs are bounded, titles escaped. A malformed/unverifiable map
cannot fabricate selectable P1. More than 1000 parts or an oversized response is
an explicit limit error, never a silently complete first-1000 result.

### `POST /api/v1/bilibili/parts:batch`

`BilibiliPartsBatchRequest`:

```text
selections:BilibiliPartSelection[1..10]
why_saved:string|null              # same existing note/collection bounds

BilibiliPartSelection:
  part_ref:bounded string
  allow_legacy_overlap:strict bool=false
```

Reject truthy strings/numeric consent, unknown fields and >10 before writes.
Require ordinary CSRF and `Idempotency-Key`. The client wrapper accepts an
AbortSignal and caller-owned stable operation key; freeze ordered selections,
proofs and destination for each attempt. Unknown-result transport retry uses the
same key/payload; intentional retry is a new operation. Expiry needs re-preview.

`BilibiliPartsBatchResponse.results` contains ordered `BilibiliPartSaveResult`:
`result_id,input_index,status,item_public_id|null,lifecycle|null,safe_error_code|null`.
Reuse existing batch outcomes; add closed statuses `part_ref_invalid`,
`part_ref_expired`, `legacy_identity_unknown`. Invalid entries have no writes and
do not stop valid peers. Worker-only source failures later surface normal item
codes `source_changed`/`source_unavailable`; queued is not a ready/subtitle promise.

### `GET /api/v1/library/items/{item_public_id}/source?t=...`

Require the current Session and owned nondeleted item. `t` is integer seconds,
default zero, bounded `0..2147483647`. Resolve its pinned CID to a current P, then
303 only to `https://www.bilibili.com/video/<BV>?p=<current>&t=<bounded>`. No caller
return URL/host/CID, no followed redirects. Deleted item, removed/ambiguous CID,
upstream/gate failure gives a fixed safe error and no wrong-part redirect. GET
never changes pin/content/ownership. Unpinned legacy links retain honest existing
provider-direct behavior rather than claiming verified CID navigation.

## Bounded public discovery and shared Redis gate

One injected public-discovery service constructs fixed HTTPS `api.bilibili.com`
paths from validated IDs, never fetches the supplied page/returned next URL, keeps
TLS verified, sends no Cookie/authentication/WBI and rejects redirects. Each call
has a 15-second whole-call deadline, streamed 1 MiB cap before JSON parsing,
strict bounded JSON fields and at most 1000 parts. Local preview pages have 20
rows. Socket timeout alone is not a whole-call bound. No whole-list yt-dlp IDs
path, per-row unbounded fetch or new background job.

Preview/source opening (and sibling favorites preview) share purpose-specific
Redis tenant rate admission 30/min plus **global** metadata upstream concurrency
at most two. Atomic expiring counters/token-fenced leases release on success,
error/cancel and expire after crashes. Redis unavailability fails closed. Count
preview/source requests; lease actual outbound calls. Signed import does not
re-fetch during HTTP admission; its pin and worker checks cover queue-delay changes.
Do not log raw URLs/bodies/refs or provider exception text.

## Signed reference protocol

HKDF-SHA256 derives a separate key from `WEB_AUTH_SECRET` with
`info=stashseek/bilibili-part-ref/v1`. HMAC-SHA256 signs bounded canonical versioned
public JSON `{bvid,cid,page,expires_at}` with a 30-minute TTL; include server tenant
identity only in MAC verification context, never descriptor payload. Validate
version/purpose/schema/no extra fields, bounds/integer expiry and constant-time
MAC. The proof is not authentication or quota bypass. Replay deduplicates normally;
tamper/expiry/other account fails per item. Keep refs only in owning in-memory
private UI/query lifetime; no Web Storage, chat history, URLs or logs.

## Shared admission and worker source fence

Prepare verified refs into server-owned typed references, then reuse the existing
prepared-batch loop, global→tenant locks, quotas, idempotency and dispatch-ID-only
publishing under one existing total publish budget. Persist source columns in the
same item/dispatch transaction. Ordinary `submit_urls` still normalizes locally.
There is no separate import coordinator/queue model.

An explicit part's admission CID is immutable across retry/restore/restart. Before
extraction, bounded fresh discovery locates its current P; absent/ambiguous CID
fails, never falls back to P1/old ordinal. Fetch only that P without playlist
expansion. **Hard gate: the actual extraction snapshot must attest its selected
CID and equal the pin.** Stable pre/post page-list responses do not prove this:
the extractor can use a differently cached initial-state page map without any
observable A→B→A reorder. CLI ordinal IDs or caller-provided CID fields are not
actual-source evidence; missing/conflicting proof fails closed.

Use a small repository-owned isolated Python API adapter/explicitly registered
`BiliBiliIE` subclass (or an equally verifiable controlled extraction boundary).
Capture the real CID argument used by `_get_subtitles(video_id,cid)` and/or
`_download_playinfo` from that same selected extraction; require all observed
source values to agree. Return only bounded projected metadata/inline-SRT plus
safe internal `source_cid`/BV evidence through the helper protocol. Do not edit
site-packages, copy the requested pin into the proof field, accept absent CID as
legacy success for explicit parts, or dump unrestricted provider output/URLs.

Configure `writesubtitles=True` (or a proven equivalent) because yt-dlp otherwise
does not invoke subtitle extraction; use `extract_info(download=False)` for
metadata only. Explicitly verify that this combination writes no subtitle/media
files, performs no media download and exposes only existing allowed inline-SRT
data. Keep no-playlist/process timeout/output bounds and redact provider logging.
Missing caption tracks still require actual selected-CID evidence before yielding
NeedsASR/NeedsExtension; absence of subtitles is not identity proof. If the current
maintained extractor cannot supply that evidence, stop as safe source-unavailable
rather than claiming support from a page-list check.

After actual-CID equality, read the bounded page map again and require that the
pinned CID still occupies the P used for extraction. This is an extra navigation
consistency guard. Moved/removed/unverifiable source fails safely; a later explicit
retry may resolve the same CID at its new P.

**All source checks precede extracted metadata/raw-pointer/raw-byte/segment
writes.** Existing early `meta.url` base-BV assignment must not erase the selected
part. After proof, re-lock and check deletion/purge/dispatch ownership/unchanged pin
before applying metadata/current URL and normal bounded raw/segment writes. No
embedding or old-segment deletion on source mismatch. SRT stays SRT; cues retain
part-local zero; stable keys isolate artifacts even for equal subtitle bytes.
Use fixed safe source failures; never rebind the pin to make a retry pass.

Do not claim remote atomic snapshots. Actual extraction CID equality is mandatory
even when both page-map checks pass; a separately verified fixed-CID acquisition
path could satisfy the same invariant, but an invented endpoint cannot.

## Generic sources versus complete browser projection

Shared Agent/MCP/channel Hit/Citation/library sources remain provider-direct
absolute URLs with validated P and local `t`. Fix the Bilibili time builder so it
retains validated `p`, discarding unrelated tracking. Never store browser-relative
resolver paths in generic sources/history consumed by other channels. Exact part
scope uses trusted item/source IDs, not mutable local URL normalization or LLM
resolver text.

At the browser API adapter only, project verified owned sources into the source
opening route. This includes library/detail, chapter actions, transcript blocks,
current JSON answers, streamed citations/final events and **reopened history**.
Use server-authorized segment/item→public-item mappings in bounded batched
lookups; no per-citation SQL and no URL-only identity guesses. Historical sources
without CID resolve from current owned segment provenance when provable, otherwise
retain honest legacy links/omit unavailable sources. Public item IDs may appear;
private integer item/tenant IDs cannot. LLM output cannot fabricate resolver links.

Browser controls consume server-issued links; a chapter control may replace only
bounded `t` on its trusted item-source URL, never reconstruct an ordinal. Preserve
all private account/QueryClient teardown semantics. Current-answer success alone
does not complete history/transcript navigation work.

Deliver a reusable feature-local part picker/direct-P entry for the dependent
favorites child. Expansion selects nothing; show parent/P/part title and selected
stable keys out of ten. Warn/require default-off per-entry legacy consent, with
cancel/skip. Re-preview may update display metadata but cannot retarget a signed
selection. Keep loading/empty/unavailable/changed states distinct.

## Validation and rollback

Prove refs/gates/HTTP limits, one-head additive migration/isolated-PG concurrent
dedupe, legacy consent, worker retry/reorder/source deletion, identical-text part
isolation, all browser projections and generic MCP regressions. Regenerate the
canonical OpenAPI/TypeScript and run relevant backend/full frontend gates plus
real-browser fake-transport picker/source flows. Live canaries stay bounded/read-
only; do not enable demo writes.

Rollback disables new routes/UI but preserves source schema/CID records/artifacts.
Code unable to understand CID-qualified IDs cannot process those items. No
destructive downgrade, P1 reinterpretation or deployment is authorized here.
