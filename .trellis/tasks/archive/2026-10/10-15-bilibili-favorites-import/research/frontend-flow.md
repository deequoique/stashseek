# Frontend flow research — 2026-10-16 (Asia/Shanghai)

Planning evidence and recommendations only. No product implementation, task start,
import, test server, migration or deployment was performed by this research.

## Evidence and boundaries

- `web/src/library/LibraryPage.tsx`: the existing “添加视频” action is gated by
  `capabilities.save_enabled`; its `AddVideosDialog` invalidates `library` after
  submission. Existing read-only demo restrictions must remain effective.
- `web/src/library/AddVideosDialog.tsx`: native modal, optional existing/new
  collection picker, a single batch request and per-entry results. Its entered
  video/BV/share-text path should remain separate from collection discovery;
  never silently treat a collection URL as a single video or expand it on save.
- `app/api/library_schemas.py:BatchSaveRequest` and
  `app/ingest/submission.py:MAX_SAVE_BATCH_SIZE`: one request accepts 1–10 entries.
  `CapabilitiesResponse.max_save_batch_size` already publishes that limit.
- `app/config.py`: defaults are 10 active dispatches per user, 50 new items per
  user/day, 1,000 stored items per user, 100 active globally, 300 new globally/day,
  and daily dispatch caps of 100/user and 1,000/global. These are configurable
  backend policies, not a promise of 10 free slots at the next click.
- `IngestQuotaPolicy.enforce` counts pending/enqueued/running dispatches, locks
  tenant/global admission and returns the same `quota_exceeded` result for the
  different limits. The browser cannot infer whether waiting a minute will fix it.
- `web/src/api/client.ts:submitVideoBatch` already sends same-origin cookies,
  CSRF and a fresh `Idempotency-Key`, but exposes neither an AbortSignal nor a
  caller-owned stable retry key. A new invocation is a new logical attempt.
- `app/ingest/submission.py:_submit_reference`: existing readable/active/archived
  videos normally return `already_exists` without changing `why_saved`; some
  previously failed items can receive a new dispatch on another request. A
  network error does not establish that nothing was saved or enqueued.
- `web/src/app/App.tsx`: confirmed logout/`session_invalid` clears and replaces
  the private QueryClient and remounts observers. Default queries do not refetch
  on window focus. There is no existing cross-tab import/auth coordinator.
- The user sample `study` returned 8 entries and no more pages in the bounded
  unauthenticated canary in [public-sample-access.md](./public-sample-access.md).
  This verifies that one sample at that time; availability, safe upstream request
  bounds and platform failures remain the root/backend research responsibility.

Read specs: frontend component, hook, state-management, type-safety and quality
guidelines. They require server-owned DTOs, query-owned reads, local ephemeral
selection, bounded retries, generated contracts, full tenant cache teardown and
no persistent account/content cache.

## Smallest verifiable version (selection confirmed, implementation not started)

Latest planning update: the user confirmed two features: public favorites import
and multi-part-video handling with expandable part selection, independent saving
and retrieval, and accurate source jumps. The user briefly requested 50 and then
explicitly withdrew that change (“不用改上限了”). **Keep the existing 10-entry
batch limit and all current quota settings. Each selected part is one entry.**
No 50-limit work is approved. Implementation still awaits review of the complete
plan and task activation by the root.

Keep the existing direct-video input and add an explicit “导入公开 B 站收藏夹”
entry within the add workflow. Use one native dialog shell with a distinct import
view; avoid nesting dialogs. A feature-local child can own preview/selection so
the direct BV/share-text form remains small. Reuse the existing collection picker
and `formatWhySaved` convention rather than inventing a second collection model.

The import view has three visible phases:

1. **读取列表**: one labelled public-collection URL field and “预览收藏夹”. A
   preview is a read with no save/queue side effect. Distinguish invalid URL,
   inaccessible/private/deleted list, upstream temporary/risk-control failure,
   and a genuinely empty public collection. Do not render fetch failure as empty.
2. **选择视频/分 P**: escaped list title and returned count/page information, video title
   or honest missing-title copy, a BV ID and native per-row checkbox. Provider-
   unavailable entries are visible with a safe reason and cannot be selected;
   do not assume a listed item has readable subtitles. The server projects
   canonical part references; frontend does not infer ownership or eligibility.
   Expand a multi-part row to select specific parts rather than silently selecting
   P1 or importing all parts. Count selected parts, not parent video rows.
3. **确认与结果**: chosen destination collection/optional note, “导入已选 N 个”
   and individual results. At most 10 selected parts are sent in one explicit
   submission, reusing existing tenant-scoped ingestion admission. The generated
   request contract may need part references; the current URLs-only API cannot
   by itself prove that a previewed P ordinal still refers to the same part.
   Import means accepted for asynchronous ingestion, not
   subtitles ready. Successful/duplicate rows leave the current pending selection;
   failed rows remain visible for explicit action. Refresh `library` in the
   background after the response.

For the first version, use **at most 10 selected parts per explicit import**,
with further manual selections after the result. Eight sample parent videos fit
one request only if the user selects no more than ten total parts. This preserves
both existing limits and needs no durable import job or
new job table. This is the user's selected option; it does not promise reliable
whole-folder background import or refresh continuation.

### Multi-part videos: confirmed feature, required UI contracts

Current ordinary BV/no-`p` admission deliberately selects the first part through
`--no-playlist`; `p=2` is currently rejected. This is existing behavior to extend,
not the final feature contract. A favorites parent row cannot be labelled “全部
分 P 已导入” based on a successful save of one part.

Recommended interaction and contract requirements:

- A single-part video can display one selectable P1 row. A multi-part parent
  displays a native expand button/details label (“展开 N 个分 P”) and loads its
  bounded part preview on demand. Expanding is read-only and selects nothing.
  Distinguish loading, unavailable part list, partial bounded list and complete
  empty result. Do not invent a one-part fallback when discovery fails.
- Child rows show server-provided current P ordinal, escaped part title, duration
  when known, and availability. A row label should identify both parent title
  and part, so multiple selected videos' P2 rows are distinguishable.
- The parent can show selected/available counts, for example “已选 2 / 8 分 P”.
  A “选择该视频全部分 P” action is allowed only for a completely loaded bounded
  part list that fits the remaining 10-entry capacity. Otherwise explain the
  capacity and retain existing selection; do not silently choose the first ten.
- Keep a sticky/always-visible “已选 7 / 10 个分 P” count and a selected-items
  summary across favorites pages and expanded video rows. The confirmation list
  includes parent title + P ordinal + part title for every submitted unit.
- Selection keys must be server-projected **stable part identity**, not a current
  array index, P ordinal or only BV. The backend chooses a safe public part-ref
  contract (for example an opaque ref bound to BV + provider part identity).
  Provider CID need not become a browser-owned admission authority.
- Freeze the ordered part refs, parent/part display snapshot and destination
  collection/note for each submission. The server must resolve/revalidate those
  refs; the browser must not manufacture `?p=N` from a stale ordinal.
- Reordering between preview and submit must not retarget a selection. Return a
  safe per-item conflict such as “分 P 列表已变化，请重新预览” if the selected
  identity no longer matches the current mapping. Preserve other results and
  let the user refresh that parent's part list and explicitly select again.
- P1 migration/compatibility is backend-owned: an existing plain-BV item and an
  explicit selection of its actual P1 must converge to the supported canonical
  item/`already_exists` behavior; frontend must not create a second “P1副本”.
  Backend should communicate canonical duplicate outcome and current public item
  ID. Existing tags/notes/history must remain intact. If historical “P1” identity
  cannot be determined, surface the backend's safe conflict instead of guessing.
- Library/detail titles must clearly distinguish saved parts, whether via
  server-composed titles or explicit part fields in generated DTOs. Independent
  saved parts must have distinct public item IDs and normal lifecycle/status.
- Detail “查看来源”, chapter jumps, transcript time links and chat citation URLs
  must preserve the correct part and timestamp. Existing frontend evidence:
  `web/src/videos/VideoDetailView.tsx:timestampUrl` currently clears all Bilibili
  search parameters before setting `t`, which would lose `p=N`; this must be
  addressed under accurate-jump implementation. Transcript `block.source_url`
  and chat `citation.url` are used directly, so their backend DTOs must already
  carry authoritative part-specific source URLs. Never strip a part parameter
  or infer another part from a transcript's timestamp.

## Pagination and selection semantics

- Fetch preview pages on demand, never eagerly enumerate an unknown collection.
  Show provider-reported totals as reported metadata, not as a completed snapshot.
  Mark a reached preview cap explicitly; do not say the whole folder was loaded.
- A proposed preview page size of 10 makes “全选本页” intuitive with the single
  import limit. The exact fixed upstream size/page/time cap belongs to backend
  design and is not established by the `ps=20` sample canary.
- Default to no selection. With parts, label the bulk action explicitly, for
  example “选择本页已显示分 P”: it means loaded eligible child rows on the current
  favorites page, not unopened parents, unseen parts or unseen favorites pages.
  It must never silently select the first 10 of a larger part list.
  If adding the page would exceed the remaining selection limit, explain the
  limit and leave the selection unchanged. “取消本页选择” only affects that page.
- Preserve explicit selections across pages within the open view, keyed by
  server-projected stable BV/part identity. Show an always-visible selected
  count and a compact selected-items summary, including off-page selections.
  Changing the source collection clears selections and prior result association.
- Do not import multiple occurrences of the same **part** twice merely because list
  pages repeat entries or the provider changes ordering. Different parts of the
  same BV remain distinct. Display duplicate-source
  occurrence information if needed; the existing backend remains authoritative
  for already stored videos and concurrent duplicate admission.
- Freeze the submitted array for each attempt and map each returned
  `input_index` to that exact array; page changes must not retarget a late result.

## Destination collection mapping

StashSeek “收藏夹” currently means a `#tag` within each video's `why_saved`, not a
separate folder entity. Reuse the existing validated optional destination:
unclassified, an existing tag, or an explicitly created tag. The selected tag and
note apply to this submission through the existing `why_saved` field.

Do not automatically turn the Bilibili folder title into a tag: a title may
contain spaces/punctuation or exceed 20 characters. It may be offered as a
clearly editable suggestion after validation. The technical default can remain
“未归类”, matching direct add. Existing/duplicate videos retain their current
note/tags under existing submission semantics; importing should not silently
retag or unarchive them. Source-list synchronization, future auto-imports and
removing local videos when the remote list changes are outside this version.

## Results, failure and retry

| Result | UI meaning and proposed next action |
| --- | --- |
| `queued` | “已添加，等待整理”; use library lifecycle for later readiness. |
| `already_exists` | “资料库中已有”; no new queue promise or note/tag overwrite. |
| `unsupported_url` / `invalid_url` | Safe per-entry failure; leave visible, no automatic retry. |
| `quota_exceeded` | “当前添加额度不足”; preserve other outcomes, no retry loop or invented reset time. |
| `queue_unavailable` / `create_failed` | Some content may already exist; show failure and explicit retry/review action. |
| Request/connection error without a body | “未能确认本次结果”; reconcile with library, do not label every item unsaved. |
| `session_invalid` | Stop, discard preview/selection/results with private-client rotation and return to login. |

A small single-batch version can offer “重试未完成项” for known failure rows only,
after another explicit click, with at most 10 entries. Never resubmit successful
rows as an automatic blanket retry. A caller-owned in-memory request key for
repeating the **same uncertain request** would require a narrow client API
extension and tests; the current helper always generates a new key. It is not
equivalent to a resumable import operation and must not be persisted in storage.
Since failed-item new attempts can intentionally requeue work, the UI must not
claim exactly-once import across a refresh.

Show accepted/duplicate/failed counts and row-level reasons. “已处理 8/8” may
describe submission responses; never label that “视频整理完成”. Use one library
page polling policy for queued/processing items rather than a new per-video poll.

## Why a frontend >10 loop is a different product

Splitting, for example, 80 selected entries into eight immediate requests of 10
does not provide reliable whole-folder import. The first request can occupy the
default 10 active slots; the second then returns quota failures. Existing jobs,
other tabs and other users can lower capacity even for the first batch. The
current public error does not distinguish concurrency from daily/storage/global
limits, so a frontend cannot safely infer when or whether to continue.

An automatic larger operation needs an explicit policy for maximum total size,
waiting versus stopping at quota, retry budget/delay and uncertain responses.
If it must survive closing the page or refreshing, it needs a server-owned
bounded operation, stable membership/progress/idempotency and session/tenant
authorization, not only component state or an unbounded browser timer. That
would be a larger design and may need durable state; this research does not
assume a new background job or migration.

## Lifecycle and cross-tab isolation

- Preview reads use TanStack Query and pass AbortSignal to the API client. Use a
  feature resource key (for example `bilibili-favorites-preview`) beneath the
  current private client; no URL/query/storage persistence of private selection
  or result state. Generate response aliases from canonical Pydantic DTOs.
- Import mutations have automatic retries disabled. Gate duplicate clicks with
  an immediate run ref as well as disabled controls; capture source, chosen URLs
  and destination once. A generation/mounted guard ignores late results after
  source change, close or private-client rotation. Abort on disposal where the
  client supports it; abortion cannot undo already committed server work.
- Closing/navigating away ends UI work and clears unsaved selection. Previously
  accepted videos continue normal ingestion. Say this plainly; do not promise
  “取消导入” rolls back saved entries. A refresh reloads the preview on demand
  and users may inspect the library; there is no resumable job in this proposal.
- Separate tabs have separate in-memory selections. Backend tenant admission and
  duplicate handling protect simultaneous attempts; frontend disabling only
  serializes its own view. Do not use localStorage as a cross-tab queue/lock.
- Account switching in another tab changes shared cookies; the current cached
  Session does not automatically detect that. Before manual confirmation, an
  authoritative Session refresh and clearing the import view on account change
  are reasonable technical safeguards. They do not create an atomic account-
  bound multi-request operation: any future automatic loop must be scoped by
  the server to its original authorized account and stop on mismatch. Never let
  a stale closure keep sending future batches after remount/logout/401 under a
  newly authenticated account's cookies.

## Decisions and defaults to surface for approval

The user has resolved both product decisions: public-list preview with at most
10 selected entries per explicit import, and expandable multi-part selection
with each part independently saved, retrieved and accurately linked. A temporary
50-entry request has been explicitly withdrawn; do not change any limit or quota.
Any future whole-folder/background mode would separately need its total cap,
quota waiting/retry and refresh/close behavior made explicit.

Page-scoped “全选”, no default selection, honest partial outcomes, reuse of
optional collection tags, no automatic retagging and no persistence are sound
technical/product defaults to state in the final scope; they do not each need
a separate question. Do not manufacture provider accessibility assurances from
this UI recommendation or ask the user to choose backend internals.

## Proposed acceptance tests once implementation is authorized

1. A disabled/unknown save capability prevents import and preserves the existing
   direct-add/demo behavior; native dialog focus/Escape and 390px layout work.
2. Preview creates no batch writes. Distinct loading, empty, invalid/private,
   upstream-error and bounded partial-preview states remain truthful.
3. Sample-shaped 8 parent rows: select none by default; expand bounded part lists,
   select visible children and import exactly their canonical part references
   once with the chosen existing/new tag or null reason. No parent checkbox may
   silently mean all unseen parts or only P1.
4. Page navigation retains explicit selections, full-page selection does not
   silently truncate, source change clears selection, and the total limit blocks
   an eleventh selected part before any request. Repeated source rows do not
   duplicate a part; P1/P2 of the same BV remain independent selected entries.
5. Partial result indices map to the frozen selection; duplicate/success rows
   are not retried; quota/queue failures retain individual feedback and invalidate
   `library`. No submission result claims subtitles/readiness.
6. Double-click, slow response, uncertain failure, close/unmount, source change,
   401 and account/client rotation cannot start another attempt or leak a late
   result to another account. Explicitly test disposal before any next batch if
   a larger mode is later added.
7. Run existing frontend tests/typecheck/lint/build/check:api, generated-contract
   checks, safe same-origin/CSRF/idempotency client tests, and real browser dialog,
   mobile overflow and console checks. Upstream public reachability is a bounded
   read-only backend canary, not a fake frontend success fixture.
8. Existing plain BV versus explicit actual P1 deduplicates to the backend's
   canonical item without losing its tags/history. A reordered/removed part
   between preview and submit receives a safe refresh/reselect outcome and never
   imports another part under the old ordinal.
9. Library/detail titles distinguish parts; source, chapter, transcript and chat
   citation links all retain the correct `p` and timestamp, including P2+ and a
   reordered current P position. No timestamp helper resets `p` to P1.

## Frontend limit/capability inventory (read-only; keep limit 10)

The abandoned 50 request was not implemented by this research. Current literal
10s remain intentional under the user's latest instruction:

- `web/src/library/AddVideosDialog.tsx`: `submittedUrls.length > 10`, over-limit
  counter, `N / 10` and heading/error copy. Existing direct-input behavior remains
  10; the new favorites/part selector must apply the same total submission cap.
- `web/src/library/AddVideosDialog.test.tsx`: existing 11-entry rejection,
  extraction limit and 1–10 submission tests remain valid. Add a new 11-selected-
  part test rather than replacing the established limit tests with 51 cases.
- `web/src/api/contracts.ts`: `Capabilities` aliases the generated schema;
  `BatchSubmitInput` aliases `BatchSaveRequest`. There is no independent handwritten
  max union to edit. `openapi.json` records maxItems/default=10; regenerate only
  for real new preview/part DTO changes, never manually change generated files.
- Current capabilities fixtures with `max_save_batch_size: 10` are in
  `LibraryPage.test.tsx`, `ChatPage.test.tsx`, `conversationQueries.test.ts` and
  `LoginPage.test.tsx` (and other app fixtures if found during implementation).
  They remain 10. `LibraryPage` currently uses `save_enabled` to gate add, while
  `AddVideosDialog` itself hardcodes 10 rather than reading the published size.
  A new import child can receive the capability's supported maximum, bounded by
  the current product/server limit; do not enable larger imports from a guessed
  default or change caps while capabilities are pending/failed.
