# Favorites UI implementation evidence — 2026-10-16

## Implemented scope

- Capability-gated add-dialog favorites view preserves ordinary BV/share input
  and explicit `?p=N` handoff to the shared parts picker.
- Favorites page reads at most 20 parents, shows reported totals/cap/unavailable
  states, and lazily expands current pages of parts with no default selection.
- Stable-key ordered selection spans favorite/part pages. Loaded-scope bulk
  selection is atomic at ten, and duplicate occurrences do not replace already
  chosen proofs. Off-page summaries retain each parent/P/title and explicit
  default-off legacy consent.
- The shared `PartImportControls` freezes proofs, ordered display/destination,
  consent and one operation key. It handles partial outcomes, synchronous
  duplicate-click prevention, uncertain same-key replay, original-attempt expiry,
  close/abort and stale private-view results. Existing ingestion quotas remain.
- Exact server-issued `expires_at` disables expired selections. A deliberately
  requested parent refresh renews only matching stable keys and clears consent
  when its compatibility notice changes. No opaque proof payload is parsed.
- API client aliases and both generated files use canonical DTOs. Favorites and
  parts reads carry AbortSignal and Session CSRF; submission reuses the foundation
  parts route and caller-owned idempotency key.

## Automated frontend gates

The complete frontend suite passed **255 tests across 24 files**. Typecheck,
lint, production build and generated API stale checks all passed after the
final product change. A subsequent correction strengthened one existing test's
assertion of the actual injected preview call; its 12-test FavoritesPicker file
passed again. No product behavior changed after that full gate.

New focused coverage includes eight-parent read-only preview, lazy expansion,
cross-page P2 identity/order/deduplication, atomic over-limit bulk selection,
legacy consent after collapse, frozen-index partial outcomes, double click,
unknown same-key retry, source-change abort/late response, disposal, private/cap
feedback, local expiry with explicit same-CID refresh, and direct `?p=21`
handoff. Pure helper tests cover identity/capacity/refresh invariants.

## Isolated fixture and browser gate

The reusable RAM-only fixture is at the multipart sibling's
`artifacts/browser_qa_api.py` and `browser_qa_vite.mjs`. It now includes canonical
favorites DTOs/capability and exact signed-reference expiry:

- Ordinary valid collection IDs show eight clearly labelled fake parents.
- `fid=22` shows pages of 20 and 2; page two repeats the first parent's BV to
  exercise deduplication. `fid=403` gives private-list feedback and `fid=429`
  gives temporary-limit feedback.
- Parent expansion has 29 fake parts in pages of 20 and 9. P3 needs legacy
  acknowledgement, P4 is unavailable. P1/2/others return duplicate/quota/queued.
- An `unknown` note makes the first result 503, preserving a RAM result for an
  identical-key replay. No item is actually imported or enqueued.
- Source actions show expected current-P6/local-time destination as JSON without
  following a real Bilibili URL.

HTTP-only fixture smoke passed 20/2 paging, repeated parent identity, zero lazy
part/batch calls during parent preview, and automatic JSX compilation. This
does not establish real UI rendering, modality, geometry or console cleanliness.

The subagent's CUA session still had no browsers after refreshing documentation,
even when the root session discovered an available IAB. The root took over
exclusive real-browser QA against 5181; final native/browser results and cleanup
must be recorded by that run. Earlier locked-device evidence remains historical
and must not be treated as a passed browser gate.

## Real-browser finding and narrow layout repair

The root's available IAB reached the actual frontend at 5181 and observed that
the old count nested in the later confirmation section was outside the visible
dialog while a long parent list was expanded. That is a real geometry finding,
not an inference from jsdom. Root retained its before screenshot.

The narrow repair puts one sticky status bar at the top level of each picker,
with the unique selected count and “查看选择与确认” action. The action scrolls to
and focuses the confirmation region. The shared import controls suppress their
duplicate count. A new interaction test covers unique status text and the
confirmation focus/scroll handoff; root owns the separate real geometry retest.

After this final product change: **256 frontend tests across 24 files passed**,
along with typecheck, lint, production build, generated API stale checks and
`git diff --check`. Real-browser acceptance remains subject to the root's final
native/modal/mobile/console/storage report.

## Root real-browser completion and cleanup

The root's IAB session subsequently completed the combined fixture run; its
aggregate parent report is authoritative. Observed checks include native modal
focus/Tab/inert background, Escape and cleared reopening; 1280×900 and 390×844
layouts; unique sticky count and confirmation focus handoff; partial results,
ten-entry/bulk guards, cross-page duplicate identity; same-key unknown-result
replay; and chapter source opening that preserved expected P6/local t185.
Browser error/warning logs were empty. The root saved desktop/mobile screenshots
under the parent task's artifacts.

The browser read-only evaluation scope did **not** expose Web Storage. Do not
label actual localStorage/sessionStorage inspection as passed. The absence of
private-ref persistence is supported by the implementation review and automated
tests, with this direct browser-inspection limitation recorded separately.

After the root closed only its QA/source tabs and restored the viewport, both
owned temporary server sessions were stopped (exit 130). Listener checks showed
8021 and 5181 free. No original demo process or user tab was stopped/changed by
this subagent. Fixture stderr included expected broken-pipe exceptions from
aborted browser requests; they contained no private data and were not browser
console/runtime errors.
