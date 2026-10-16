# Independent favorites and combined-contract review — 2026-10-16

## Result

**Product code/protocol review passes after the rate-classification fix below.**
Favorites reuses the independently reviewed multipart foundation; there is no
second admission, identity, Session or source resolver. No blocking code defect
remains from this review. Real Redis Lua evidence was subsequently completed and
independently reviewed. The root owns final native browser acceptance; neither
that check nor this code review is deployment approval.

I loaded the child PRD/design/execution plan, all fifteen check-context references,
parent requirements/design, prior planning review, foundation review and current
canary records. Previously loaded full retrieval/citation guidelines were retained;
new TLS/logging/directory guidelines were read in full. Review included strict
parsing/transport/service, canonical DTO/routes/capabilities/runtime/exporter,
refs/expiry, FavoritesPicker/PartRows/PartImportControls/selection helpers,
AddVideos/Library capability wiring, API client/generated contracts and CSS/tests.

The listed `app/api/auth_schemas.py` changes are existing external-login WIP,
not a favorites-owned auth protocol change. They were preserved.

## Finding fixed

The approved requirements distinguish upstream rate/risk blocking from ordinary
temporary failure, but JSON provider code -352 and HTTP429 initially collapsed
into `bilibili_unavailable`. The existing connector and installed maintained
extractor already recognize -352. The public JSON boundary now maps it to the
existing `bilibili_rate_limited` code for both favorites and parts. The HTTP
child projects HTTP429 through a fixed exit3; the parent converts that exit to
the same safe error without reading/exposing provider bodies or exception text.
No API/DTO, timeout, host, quota or limit was broadened.

Reviewer edits are `app/bilibili/public.py`, `app/bilibili/fetch_public.py` and
`tests/test_bilibili_favorites.py`. Three new regressions cover JSON risk code,
the child/parent fixed-exit projection and redacted HTTP429. The backend owner
updated the executable spec's safe-error matrix to match.

The two earlier foundation review fixes (known-CID legacy retry and truthful
preview-cap feedback) are recorded in the multipart child's independent report
and remain intact after the shared controls were extracted.

## Cross-child acceptance review

| Parent acceptance | Reviewed code/evidence |
| --- | --- |
| AC1 public read-only list | Strict space/favlist and detail/ml aliases yield only numeric IDs. Fixed host/path/ps20, one page per call, bounded strings/count/body/deadline and shared gate. List preview does no item/dispatch/per-video work. Empty/private/error/cap outcomes differ. |
| AC2 explicit independent parts | Lazy parent expansion selects nothing; direct P draft preserves its selector and foundation preview chooses its containing page. Each chosen CID is independently admitted and extracted. |
| AC3 counts/dedupe/limits | Stable BV:CID keys retain ordered cross-page choices; duplicate occurrences collapse. Loaded-scope bulk union is atomic and rejects >10. All existing request/quota/worker limits remain unchanged. |
| AC4 text/time attribution | Foundation actual-CID proof, CID-qualified source identity and separate raw/item namespaces preserve equal-text parts and local time axes; browser transcript and generic citations retain attribution. |
| AC5 accurate navigation | Detail/chapter/transcript/current JSON/SSE/reopened history use owned resolver paths. Current CID→P lookup and bounded local time prevent stale-ordinal/P1 fallback. |
| AC6 mutation/retry/source fence | Signed admission pins survive queue delay and retry; actual extraction CID plus pre/post mapping and ownership locks precede content mutation. Known legacy key retry preserves its identity. |
| AC7 partial/unknown outcomes | PartImportControls freezes ordered refs/consent/destination/key/display snapshot; indices target that snapshot. Accepted rows leave selection; failures stay visible. Unknown response retains exact same-key replay, with no automatic batches or caption-ready claim. |
| AC8 old content/history | Unknown legacy CID remains NULL and requires default-off per-entry consent before separate saving. Existing known-CID rows dedupe. Old notes/IDs/history are not merged, copied or backfilled. |
| AC9 auth and private lifecycle | Canonical Session/Origin/CSRF/no-store, server tenant refs, strict requests, abortable reads, synchronous run guard and unmount generations. No refs/results in storage/router state. Global private-client rotation remains authoritative. |
| AC10 regressions/demo | Ordinary direct BV/share/other-platform handling stays separate. Capability gating requires save support and both relevant services for favorites. Read-only demo writes remain disabled. |

Part-import destination/tag/note controls are disabled while the attempt is busy
or unknown; a later edit cannot silently replace a frozen request. Explicit
refresh updates only matching stable keys and clears consent when compatibility
assumptions change. It does not select a new occupant of an old ordinal.

## Independent verification

- **99 passed:** favorites, parts core and parts API tests after the safe-rate fix.
- **67 passed** after the later selection-toolbar fix: five frontend files: favorites picker, parts picker, pure
  selection helpers, add-dialog integration and API client.
- Canonical OpenAPI/TypeScript `pnpm check:api` passed; diff whitespace check passed.
- Foundation review independently ran **111** extractor/worker/retrieval tests,
  **27** earlier UI tests, **14 real isolated PostgreSQL** tests, frontend lint
  and typecheck. Its report describes schema/concurrency/migration limits.
- Frontend owner handed off **256 tests plus typecheck/lint/build/check:api**
  passing. I did not repeat unchanged full frontend gates unnecessarily.
- The root's final full backend run recorded **1216 passed, 74 skipped,
  2 pre-existing date-dependent failures** in item-management tests. The two
  fixed-date restore/resave fixtures expect restoration although their seeded
  deletion date is now beyond retention. They are not silently changed here.
  This final run includes the safe-rate fix/tests and precedes addition of the
  separately executed six opt-in Redis tests; do not sum them into a fictional
  single full-suite run. Evidence: parent `artifacts/combined-backend-final.xml`.

## Live evidence and remaining gates

- Root new-client public favorites canary: `study`, reported/returned 8,
  has_more=false, one bounded read, no import/DB/queue operation. This is current
  provider/host compatibility only, not production egress or subtitle assurance.
- Root controlled actual-extractor canary verified P2 and expected CID1432055578,
  matching actual extraction evidence and stable pre/post page. Result was
  **NeedsASR, zero cues**, no media/subtitle files or ingestion. This proves the
  real no-caption hook path; it does not claim available captions. See parent
  `research/controlled-part-canary-20261016.json`.
- Native acceptance was initially blocked by the locked desktop. The root later
  obtained IAB access and found that the selection count could scroll away with
  long expanded lists. The reviewed narrow fix adds one sticky toolbar/count
  and a keyboard-focusable confirmation jump; details are below. The root owns
  the final native recheck and its evidence; component tests do not replace it.
- Six real Redis Lua tests now pass on a separately owned temporary Redis 6.2.11
  server, covering actual natural expiry and concurrent admission. I reviewed
  the fixture/test logic and independently matched jar/binary hashes to its
  record without repeating the natural-wait test. See multipart
  `research/redis-live-gate-success.md` and its independent-review addendum.
  Earlier official-source timeout attempts are preserved as historical evidence.
- The recorded pre-task temporary baseline directory is missing, confirmed by
  the root. This review cannot claim a bytewise baseline comparison; focused
  ownership/diffs/regressions were used without restoring or staging prior WIP.
- No actual sample import, user-account modification, production/shared
  migration, deployment, Git commit or push was performed by this review.

## Later native-feedback layout review

The root's real-browser feedback justified a narrow layout correction. Both
pickers now expose a single top-level sticky `PartSelectionToolbar`; common
import controls hide their repeated count. “查看选择与确认” scrolls instantly to
and focuses the labelled confirmation region, with a scroll margin for the
toolbar. The button is non-submitting and disabled for zero selections, so it
does not alter refs, selection, consent or import attempts. CSS retains the
bounded native form/list scroll regions and uses the established paper palette.
The change also counts expiry from a frozen unknown attempt when present.

I reviewed these stable changes after the owner handoff and reran the five
focused frontend files: **67 passed**, including the single-count and focus-jump
regression. Canonical generated-contract checks passed again. The owner reran
all frontend gates after the layout fix with **256 passing tests**. No further
code fix was needed; actual sticky geometry/mobile behavior remains the root's
native recheck rather than an assertion inferred from jsdom.
