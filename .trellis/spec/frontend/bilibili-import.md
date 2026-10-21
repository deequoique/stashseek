# Public Bilibili Favorites and Part Selection

## 1. Scope / Trigger

Apply when changing public favorites preview, explicit multipart selection,
Add Videos modes, signed-reference lifetimes, selection confirmation/results, or
browser navigation for independently saved Bilibili parts. Backend identity,
quota, worker proof and source opening remain defined by
[Bilibili parts](../backend/bilibili-parts.md).

## 2. Signatures

```typescript
previewBilibiliFavorites(input: BilibiliFavoritesPreviewInput, signal?: AbortSignal)
previewBilibiliParts(input: BilibiliPartsPreviewInput, signal?: AbortSignal)
submitBilibiliParts(input: BilibiliPartsBatchInput, operationKey: string, signal?: AbortSignal)

interface SelectedPart {
  part: BilibiliPartPreview;
  parentTitle: string;
  allowLegacyOverlap: boolean;
}

addLoadedParts(selected: readonly SelectedPart[], loaded: readonly SelectedPart[]): SelectedPart[] | null
refreshSelectedParts(selected: readonly SelectedPart[], refreshed: readonly SelectedPart[]): SelectedPart[]
```

`FavoritesPicker`, `PartsPicker`, shared `PartRows` and `PartImportControls` live
under `web/src/library/`. DTO aliases come from generated Pydantic contracts.
The API client owns all fetch/Session-CSRF/idempotency behavior. Ordinary BV/share
batch submission keeps its existing URL-only API.

## 3. Contracts

- `LibraryPage` gates writable entry on `save_enabled`; part selection also needs
  `bilibili_parts_enabled`, and favorites needs both part and
  `bilibili_favorites_enabled`. Missing/false capability never enables the entry.
  Preserve the read-only demo and direct BV/share form.
- One native add dialog owns ordinary URLs, explicit parts, and public favorites
  views. An explicit `?p=N` draft can enter the parts view without dropping `p`;
  preview selects nothing and still requires deliberate confirmation.
- Favorites reads one page of at most 20 parents. Show provider-reported total,
  actual page, unavailable rows and the explicit page-500 cap. Do not report an
  inaccessible collection as empty or promise an immutable complete snapshot.
- A native parent disclosure lazily loads one page of at most 20 parts through
  the foundation API. Opening/collapsing a parent never saves or implicitly
  selects P1/all parts. Failed discovery never fabricates a single-part fallback.
- Selection is ordered, ephemeral and keyed by server `part_key`, independent of
  current P ordinal, rendered array index or refreshing signature. P1/P2 of one
  BV are distinct; duplicate occurrences of one proven part select once.
- The limit remains **10 selected parts**, with one part per item/dispatch and
  all backend quotas unchanged. “选择本页已显示分 P” means only eligible rows
  already loaded and displayed on the current favorites page. Its union is
  atomic: more than ten leaves the old selection intact with explicit feedback.
  No eager traversal, silent truncation or automatic batch loop is permitted.
- Keep off-page selections visible in the selected summary. Unknown legacy
  overlap is disclosed before import and requires default-off per-entry consent.
  The summary permits that consent even after its parent is collapsed or paged
  away. General import confirmation never grants overlap automatically.
- A picker-level sticky status bar keeps the single selected-count label and a
  “查看选择与确认” action visible while long parent/part lists scroll. That action
  scrolls to and focuses the confirmation region without animation. A count
  nested only inside a later confirmation section cannot meet this requirement.
- Server-issued `expires_at` is only a UI aid; the server remains authoritative.
  Disable expired submissions and uncertain replays. An explicit parent refresh
  can renew only the same stable keys, never another occupant of an old ordinal;
  changed compatibility notices clear old consent. Never parse opaque proof
  payloads or automatically refresh/re-submit an unknown attempt.
- `PartImportControls` captures a deep display/ref snapshot, ordered payload,
  destination, consent flags and caller-owned key for each attempt. A synchronous
  run ref blocks duplicate clicks. Unknown-result retry reuses that exact key and
  payload, including the original proof expiry. Controls cannot quietly create a
  new operation after an uncertain response.
- Map result `input_index` to the frozen snapshot. Remove known queued/duplicate
  selections, retain individual failures and refresh `library` in the background.
  “已添加，等待整理” is admission, not a subtitle/readiness promise. Queue failures
  may already have an item; point users to library state rather than claiming
  nothing was saved or blanket-retrying successful siblings.
- Close/source change/unmount/private-client rotation aborts reads/imports and
  ignores late responses. Accepted work continues normal ingestion. No refs,
  selection, results, URLs or operation keys enter Web Storage, router state or a
  cross-tab queue. `session_invalid` retains global full-private-client teardown.
- Browser details/chapters/transcript/chat consume server-owned source URLs.
  Chapter controls may replace only bounded `t` on the trusted source route.
  They must not reconstruct `p` from a preview or model-authored URL. Honest
  unpinned legacy links retain validated P when changing local time.

## 4. Validation & Error Matrix

| Situation | Required behavior |
| --- | --- |
| Valid public folder, no expansion | Parent preview only; zero part-save calls and zero selected parts. |
| Private/unavailable/invalid/oversized response | Distinct bounded error; no empty-success fallback. |
| Parent unavailable or parts fetch fails | Unselectable row or visible error; no invented P1. |
| Repeated parent, off-page selection, distinct P2s | Stable key dedupe; selection order and parent labels remain clear. |
| Loaded bulk union exceeds ten | Reject atomically; preserve current selection. |
| Unknown legacy overlap | Default-off per-entry acknowledgement or skip; history remains intact. |
| Ref expires or same key is explicitly refreshed | Disable old proof; renew exact matching key, clear changed consent. |
| Partial or uncertain result | Frozen row mapping; same-operation explicit replay or inspect library. |
| Close, source change, disposal, account/401 boundary | Abort/ignore late state; no following imports and no private storage. |

## 5. Good / Base / Bad Cases

- Good: select course A P2, change favorites page, then select course B P2. Two
  stable proofs are submitted in that order with distinct parent titles.
- Good: eleven loaded rows cannot fit. Bulk selection changes nothing and says
  why; the user may choose individual parts without hidden truncation.
- Base: ordinary pasted BV/share text still uses the established URL batch and
  does not perform per-keystroke metadata requests.
- Bad: use `p=2` or list position as checkbox identity, select all unseen parts
  on expansion, refresh an unknown attempt under a different account, or retry
  every original row with a newly generated operation key.

## 6. Tests Required

- `FavoritesPicker.test.tsx`: eight-parent read-only preview, lazy expansion,
  cross-page identity/order/dedupe, loaded-scope cap, legacy consent after collapse,
  frozen partial results, duplicate click, unknown same-key replay, source-change
  abort/late response, disposal, private/cap feedback, exact-key expiry refresh.
- `partSelection.test.ts`: atomic capacity, duplicate proof preservation,
  unavailable filtering and explicit exact-key refresh/changed consent.
- `PartsPicker.test.tsx`, `AddVideosDialog.test.tsx` and `client.test.ts`: direct-P
  draft preservation, original BV/share regressions, no auto-selection, expiry,
  Session-CSRF/AbortSignal/caller-owned key and private result isolation.
- Source tests cover server-owned detail/chapters/transcript/current JSON/SSE/
  reopened history alongside unchanged provider-direct generic-channel links.
- Run all frontend tests, typecheck, lint, build and `check:api`. Real browser QA
  must separately cover native modality/focus/Escape, desktop and 390×844,
  expanded controls/summary visibility, clean console and empty private storage.
  Fixture HTTP/unit tests do not replace that browser gate.

## 7. Wrong vs Correct

Wrong:

```typescript
selected.push({ url: `${videoUrl}?p=${rowIndex + 1}` });
for (const batch of splitIntoTens(selected)) await submitVideoBatch(batch);
```

Correct:

```typescript
const next = addLoadedParts(selected, displayedVerifiedParts);
if (next === null) showCapacityError();
else setSelected(next);
// Explicit confirmation later freezes selected server proofs and one key.
await submitBilibiliParts(frozenInput, frozenOperationKey, signal);
```
