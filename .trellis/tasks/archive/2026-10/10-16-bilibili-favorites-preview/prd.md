# 公开B站收藏夹与分P选择导入界面

## Goal

Let a signed-in user preview a public Bilibili favorites collection, expand videos
into parts, explicitly select at most ten parts and import them with truthful
per-part results. Let an explicit part URL enter the same selection flow without
silently saving a different part.

## Source and Dependency

Parent: `.trellis/tasks/10-15-bilibili-favorites-import`. The user has confirmed
public favorites import and independently saved/searchable multi-part videos
with accurate source jumps. The temporary fifty-entry request was withdrawn;
the existing ten-entry limit and all ingestion quotas stay unchanged.

Depends on `.trellis/tasks/10-16-bilibili-multipart`: its stable-part admission,
tenant-bound short-lived references, source metadata, compatibility behavior and
source-opening route must be implemented and verified before this child starts
implementation. This task remains planning; no code/start/import is authorized
by the presence of these artifacts. Final plan review is still required.

## Requirements

- R1: Accept the supported public `space.bilibili.com/.../favlist?fid=...` and
  Bilibili `medialist/detail/ml...` collection forms. Preview one bounded page at a
  time without saving videos, enqueueing work or requesting Bilibili credentials.
- R2: Show list title, reported count, page status, unavailable entries and
  explicit loading/empty/error states. Public access or subtitles must not be
  promised merely because a collection URL is recognized.
- R3: Expand a video on demand into independently selectable parts. Show parent
  title, P ordinal and part title; expansion selects nothing and never silently
  means “all parts” or P1. Unresolved/unavailable parts are not importable.
- R4: Keep selections across preview pages within the open view, including parts
  from different parent videos. Deduplicate the same proven part while allowing
  P1/P2 of one BV to be selected separately. Each selected part counts as one of
  the unchanged maximum ten entries.
- R5: Make bulk selection's loaded scope explicit. Do not fetch/select unseen
  pages or truncate a larger selection to ten without the user's knowledge.
- R6: Import only after an explicit confirmation, preserving selected part
  identity, order and destination. Reuse optional existing/new StashSeek collection
  tags and save notes; do not silently retag existing items or synchronize future
  changes to the remote favorites collection.
- R7: Return understandable per-part accepted/duplicate/quota/source/unavailable/
  failure results, preserve successful siblings and distinguish submission from
  completed subtitle processing. Retry only an explicit intended attempt.
- R8: Preserve every legacy BV-only item's text, identifiers, history and tags.
  If a selected explicit part coexists with an old P1 of unknown CID, explain:
  “已有旧P1记录，无法确认对应分P；本次会新增独立分P记录”. Only ordinary explicit
  submission with the compatibility acknowledgement checked creates the new
  part item; its default is unchecked. Never copy old subtitles, backfill proof,
  overwrite the old item or label the unknown old record as a verified match.
- R9: Do not retarget selected parts when the provider reorders/removes pages.
  Source actions for imported parts use the foundation's server-owned source
  resolver, including correct part-local timestamps, rather than trusting a
  previously observed `p=N` URL indefinitely.
- R10: Closing, refresh, account change and `session_invalid` stop UI work and
  clear unsaved/private state. Already accepted videos continue their existing
  ingestion; no resumable whole-folder/background import is promised.
- R11: Preserve ordinary direct BV/share/video input, supported platforms,
  save-disabled demo behavior, SSO and chat/history work. Explicit `?p=N` input
  has an intentional parts-preview action and must never lose its selector.

## Acceptance Criteria

- [ ] AC1: The supplied public `study` collection's eight-row shape is previewed
  read-only with no batch write. Supported aliases resolve to the same collection;
  unsafe URLs, private/deleted lists, timeout/risk-control and malformed responses
  produce bounded truthful feedback.
- [ ] AC2: One preview request fetches no more than one 20-entry page; collection
  paging caps at page 500 (10,000 parent entries), and foundation parts at 1,000
  parts (50 local pages of 20). Paging is
  explicit; total/has-more/capped states do not claim a complete immutable list.
- [ ] AC3: Expanding parent videos loads bounded parts through the verified
  foundation API, selects nothing and presents distinct parent/part labels.
- [ ] AC4: Cross-page selection preserves stable part identity and order. A
  duplicate occurrence selects once; distinct parts select separately. Bulk
  actions affect only labelled loaded entries; the eleventh part is blocked
  before a write, and existing ingestion quotas/settings are unchanged.
- [ ] AC5: Confirmation submits a frozen ordered set of at most ten verified
  references with the chosen note/tag. Returned indices map to the same snapshot
  despite later page/expansion changes. No browser title/CID/ordinal is authority.
- [ ] AC6: Legacy unknown-P1 coexistence is disclosed before confirmation with
  an unchecked acknowledgement; only checking it allows the affected selection's
  `allow_legacy_overlap=true` in the frozen submission. The
  new explicit part is created only on that action, legacy content stays intact
  and a later import of the same proven part deduplicates normally.
- [ ] AC7: Partial quota/queue/source failures retain successful outcomes;
  unknown transport results are not labelled “nothing saved”. Double-click and
  uncertain-response retry do not create an unintended fresh attempt.
- [ ] AC8: Reordered/removed parts, expired/tampered refs and another account's
  refs fail or resolve to the originally selected stable part, never another P.
  Source/chapter/transcript/chat actions use the foundation's correct resolver.
- [ ] AC9: Close/unmount/source change/401/account rotation ignores late results
  and issues no subsequent imports. No references/selection/results persist in
  Web Storage, router state or cross-tab queues.
- [ ] AC10: Generic BV/share/direct URL tests, generated-contract checks and all
  relevant backend/frontend gates pass. Real browser verification covers native
  modal focus, keyboard selection, 390×844 overflow, clean console and demo gating.

## Out of Scope

Private/account-authorized collections, cookies/SESSDATA/WBI acquisition, short
links and other list kinds; unbounded collection traversal; automatic background
whole-folder imports or durable refresh continuation; increasing limits or worker
concurrency; subtitle availability guarantees; Bilibili extension capture;
implicit consolidation/relabeling of legacy text; deployment or production import.
