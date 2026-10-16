# Public favorites and part-selection design

## Status, dependency and precedence

Planning only. This child starts implementation only after the parent final-plan
approval and the verified `.trellis/tasks/10-16-bilibili-multipart` foundation.
The latest root decisions below supersede earlier alternatives in parent research:

- Stable `(BV, CID)` source identity with typed source metadata is foundation-
  owned; one selected part is one saved item and one unit of existing admission.
- Preview returns opaque signed tenant-bound `part_ref` values with 30-minute
  expiry and stable public `part_key` (`BV:CID`) for UI deduplication only.
- Existing unknown-CID BV/P1 rows remain unknown. Explicit confirmed import may
  add a new independent CID item alongside the legacy row after truthful copy
  and an explicit initially unchecked compatibility acknowledgement;
  it never copies old text, backfills proof or silently overwrites/merges it.
- Accurate source opening resolves stored CID to the provider's current P at the
  user's click. Direct ordinal links alone do not provide reorder-safe accuracy.
- Maximum batch/selection remains 10; existing active/daily/storage/global/
  dispatch quotas and worker concurrency remain unchanged.

This child owns bounded favorites discovery and the browser selection workflow.
It consumes foundation parts/admission/source APIs; it does not introduce a
second part identity, resolver, Session authority or ingestion implementation.

## User flow and component boundaries

Keep `LibraryPage` as library/capability query owner. Gate import on authoritative
`save_enabled` and available discovery/parts capability, preserving the read-only
demo. Add explicit entry actions beside the ordinary direct-video workflow:
“导入公开 B 站收藏夹” and “选择 B 站分 P”. One native modal owns input → preview →
selection → confirmation/results; no nested dialogs. Feature-local components
under `web/src/library/` separate favorites paging, expanded parts and selection
summary. Reuse collection-name validation/`formatWhySaved`; extract a small shared
picker only if the direct and import views need it, with regression coverage.

The ordinary BV/share form retains its established behavior. Its explicit parts
entry can carry an untrusted URL draft into the new preview form in component
memory. For an explicit `?p=N` draft, offer a clear parts-preview action and keep
the selector visible; never remove it and submit a base BV. Discovery resolves
that symbolic input through foundation validation, highlights the intended part
if current metadata supports it and still requires explicit selection/confirmation.
Do not run per-keystroke or unbounded network expansion in `parseVideoInput`.

Favorites preview lists 20 parent entries per page, with an explicit maximum of
500 pages/10,000 parent entries; no eager traversal is permitted. A parent expansion is read-
only and lazy, with native `<details>`/button semantics and labelled loading,
error, unavailable and bounded/capped states. It never auto-selects P1/all parts.
Each child row shows parent title, current P ordinal, part title and optional
duration. Foundation parts discovery caps at 1,000 parts per video, presented
in local pages of 20 (at most 50). Both cap states are visible; neither implies
that unseen entries were fetched or imported. No fallback to one part when discovery fails. List titles are escaped
text; cover images can be omitted in this first version.

An always-visible summary says “已选 N / 10 个分 P”, including off-page selections.
Parent counts and part counts are separate. “选择本页已显示分 P” affects only loaded,
eligible child rows on the current favorites page. “选择该视频全部分 P” requires a
complete bounded part list. Both operations are atomic in local state: if their
union exceeds 10, retain the prior selection and show the limit rather than
silently truncating. Clearing a page affects only its displayed selected parts.

Changing source collection clears selection/results and cancels prior reads.
Paging/collapsing retains explicit selection. Provider list mutation may cause
repeated parents; selection deduplicates by stable source-part key. There is no
“全收藏夹导入” action, eager traversal or browser chunk-processing loop.

## API and DTO contracts

All routes use the canonical `app/api/app.py`/runtime composition and authenticated
tenant scope, safe bounded error envelope and `Cache-Control: no-store`. Preview
URLs and part refs are POST bodies, never access-log/query-string data. All
POSTs reuse exact Origin/CSRF; imports also require tenant-scoped idempotency. Generate aliases
from canonical Pydantic models; do not hand-edit generated files or duplicate
response unions in components.

The root and foundation owner froze these shared boundaries during planning:

| Boundary | Contract |
| --- | --- |
| `POST /api/v1/bilibili/favorites:preview` | This child owns one bounded public-list page. Body `{url, page=1}`; `url` ≤2048 characters, page 1–500; server fixes page size 20. No user-chosen upstream destination. |
| `POST /api/v1/bilibili/parts:preview` | Foundation body `{url, page=1}` returns `BilibiliPartsPreviewResponse` with at most 20 part rows and 30-minute signed refs, without save/queue effects. Explicit `?p=N` remains meaningful input. |
| `POST /api/v1/bilibili/parts:batch` | Foundation body `{selections:[{part_ref, allow_legacy_overlap=false}], why_saved?}`, 1–10 entries and idempotency, returns ordered `BilibiliPartsBatchResponse`. |
| `GET /api/v1/library/items/{item_public_id}/source?t=...` | Foundation ownership check, bounded part-local timestamp, current-CID resolution and 303 to fixed Bilibili origin, or safe unavailable outcome. |

Favorites DTO: safe collection identity/title/reported total, page, fixed
`page_size=20`, `has_more` and/or explicit cap state; at most 20 parent rows with
validated BV, title and availability/reason. Reported totals are not a snapshot
guarantee. Parent rows do not carry import proofs for unexpanded parts.

Foundation parts DTO rows expose server-projected `part_key` (`BV:CID`), opaque
`part_ref`, `page` (the part ordinal, distinct from preview-list pagination),
`title`, `duration_sec` and `has_unverified_legacy_item`; parent/page metadata and
expiry accompany the preview response. Optional availability follows canonical
foundation types. A refreshing token must not be the checkbox identity, and a
mutable P ordinal must not be the dedupe key. `part_key` is public stable display/
deduplication metadata, never an authorization or admission proof.
Part refs remain only in current private QueryClient/component memory, never
location/router state, storage or logs.

Track the returned expiry while selected. An expired reference disables its
submission until an explicit preview refresh; local clocks are only a UI aid and
server validation remains authoritative. Refresh can retain the same `part_key`
choice while replacing its ref/display metadata, but never replace that key with
another occupant of an old ordinal. Clear overlap acknowledgement if a newly
returned compatibility notice changes the prior assumptions. Do not refresh and
submit a selected part automatically after account switch or an unknown attempt.

For an unknown legacy P1, foundation preview must project a safe compatibility
notice, not a fabricated proven-CID duplicate. Confirmation copy is explicit:
“已有旧P1记录，无法确认对应分P；本次会新增独立分P记录”. It appears in the selected
summary before submit with an initially unchecked acknowledgement for the
affected selections. Only the explicit acknowledgement sets those entries'
`allow_legacy_overlap=true`; pressing the general import button alone does not
silently grant overlap. A user can cancel/deselect the affected part. Freeze the
flag with the submitted selection; false remains the server default and an
unacknowledged overlap returns a safe per-item outcome. Results distinguish a newly admitted explicit item
from the preserved old item. A proven existing same-CID item returns the normal
canonical duplicate result/public ID. Ordinary BV dedupe remains legacy behavior.

Batch-parts verifies each reference's signature, expiry and tenant locally and
persists its typed immutable source pin. It does not synchronously refetch ten
provider descriptors during admission. Client titles/CIDs/ordinals/keys/URLs
cannot substitute for proof. The worker verifies the current source and actual
extractor-selected CID before applying any content; a moved CID may resolve to
its current P and a missing/unverifiable CID becomes a safe item failure. Refresh
never silently selects the new occupant of an old P ordinal. No provider request
runs inside an admission row-lock transaction.

## Favorites discovery boundary

New local parser admits only credential-free HTTPS:

- `space.bilibili.com/<positive uid>/favlist?fid=<positive id>` (the supplied
  optional `ftype=create`/known tracking values do not choose a fetch target).
- `bilibili.com`/`www.bilibili.com/medialist/detail/ml<positive id>`, subject to
  the same strict ID contract. No additional medialist/play/list alias is added.

Reject explicit ports/fragments, ambiguous duplicate identifiers, path/host
confusion, short links, unsupported list kinds and malformed input. Bound raw
input and ID lengths. Parsing yields a collection ID, never a fetchable user URL.

Use the foundation's fixed-host public discovery transport with a dedicated
favorites operation. Construct only HTTPS `api.bilibili.com` plus
`/x/v3/fav/resource/list` and server-owned validated `media_id`, `pn`, `ps=20`.
One upstream page/request, `1 ≤ pn ≤ 500`, 1 MiB decoded/body cap and 15-second
whole-call deadline, verified configured TLS, no redirects or Bilibili credentials.
Do not invoke whole-list IDs extraction and then limit printed output. Do not
follow any provider `next` URL or load every parent video's parts for a list page.

Reuse the foundation's separately budgeted discovery concurrency/rate guard;
favorites consumes that preview budget rather than changing ingestion quota or
login buckets. If that infrastructure is unavailable, fail safely instead of
performing unbudgeted requests. Keep outbound request-body/title/token data out
of diagnostics; use fixed error codes and operation/timing/count data only.

Validate response object/types/counts, `has_more`, IDs, plain bounded strings and
each returned BV. Unavailable/deleted/non-video/invalid entries remain unavailable
rows or explicitly counted skips; never convert arbitrary provider URLs to video
references. A malformed/rate-limited/private/unavailable response is not a
successful empty collection. The last permitted page displays a cap outcome
instead of promising all remaining entries were loaded.

## State, requests and account isolation

Use `useQuery` with AbortSignal for favorites/parts reads and private resource
keys such as `bilibili-favorites-preview` and the foundation's shared part-preview
options. Pure selection/dedupe logic stays feature-local and testable as a pure
function. No global store, persistent query cache, localStorage lock or cross-tab
background queue is introduced.

Local state owns source draft, open parents, selected stable keys/refs, destination
and safe result presentation. A submission captures an immutable ordered array
of refs and display snapshot, collection/note, a request key and request generation.
Disable changes or keep editing clearly separate from that snapshot; response
`input_index` always maps to the frozen submission, not current page order.

An immediate run ref plus disabled controls prevents repeated clicks; mutation
automatic retry is off. Add a narrow optional `signal`/caller-owned idempotency-key
client contract for the new parts submission if foundation has not done so.
Retrying an uncertain response reuses the exact key, exact ordered payload and
still-valid same-account references in memory. A changed selection/note is a
new confirmed operation. Existing per-item key replay is not an immutable batch
ledger; no exact report/continuation is promised after refresh.

On close/source change/unmount/client rotation, increment generation, abort
requests and reject late state updates. Already committed ingestion cannot be
rolled back by fetch abortion. No future imports are scheduled. Confirmed
`session_invalid` uses the global full-client teardown; operation-level source/
reference errors stay local. Refs bound to one tenant reject another account's
cookie even if it changes in another tab. Do not automatically refresh the refs
under a new account and carry forward the old private selection.

## Results and explicit retry

| Outcome | UI behavior |
| --- | --- |
| Queued | “已添加，等待整理”; later status comes from library lifecycle. |
| Proven same-part duplicate | “资料库中已有此分P”; preserve existing tags/history. |
| Legacy notice + new confirmed item | Keep notice and describe the new independent part result; never claim historical CID verification. |
| Quota limit | Preserve siblings, say current limit prevented this entry; do not invent reset time or loop. |
| Expired/tampered/changed/removed reference | Refresh/reselect the affected parent explicitly; never fall back to another P. |
| Queue/create failure | Some content may already exist; show known per-item public ID/reason and server-supported action. |
| No trustworthy response | “未能确认本次结果”; keep frozen attempt for bounded same-key retry or inspect library. |

Do not blanket retry successful/duplicate entries. For known failed items with a
public ID, use server-provided retry actions; same-key replay of queue failure is
not a new retry command. For expired unknown attempts, explain uncertainty and
reconcile with library before another new confirmation rather than bypassing ref
expiry. Refresh `library` in the background after known results; no new per-part
poller or guessed percentage. Counts describe submission responses, not completed
transcription. Closing says accepted videos continue processing.

## Sources and accurate jumps

Consume the foundation's server-owned source links for saved part source,
chapter, transcript and chat citation actions. Do not append `p` from a preview
ordinal to a model-authored or stale external URL. The foundation owns the source
metadata migration/resolver and generic citation validation. This child's UI
integration tests prove it uses those URLs without stripping resolver paths or
part-local timestamps. The known Bilibili `parsed.search = ""` timestamp helper
must be removed/replaced by the foundation integration, never shipped unchanged
for new part records. Ordinary legacy links keep their disclosed old limitation.

## Rollout, rollback and external limits

Activate only when verified foundation composition and capabilities are present;
an unavailable backend disables the new entry with safe feedback. Existing direct
video add remains usable. Rollback can hide the new preview/import entry without
deleting saved parts/source fields; this child adds no database migration, import
job or Celery coordinator. Foundation data rollback is separate and must not erase
new part/history records.

The eight-row public sample proves only point-in-time anonymous first-page access
from one host. Fixed public endpoints may change or block production egress; no
official support or subtitle-access guarantee is made. Provider changes/reorder
can make a part unavailable. Source resolution fails safely instead of knowingly
opening another part. Preview is not permission bypass, account authorization or
a promise that the current browser extension can capture Bilibili subtitles.
