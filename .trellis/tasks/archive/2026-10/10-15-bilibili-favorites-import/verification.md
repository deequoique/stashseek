# Combined implementation verification — 2026-10-16

## Final local commit outcome

User-approved work commits are complete: `b4d60fe` (chat/history), `2bb6dbc`
(external login) and `788b2ae` (public favorites/multipart). All three actual
indexes passed independent path/blob/exclusion checks before committing. C had
147 explicit files, byte-identical to its frozen reviewed workspace manifest.
The eight copied pytest logs also had trailing whitespace stripped; XML results
and failure identities are unchanged, with final hashes in log-redactions.json.

Current completion evidence is this section and the commit-preparation follow-up.
The later delivery/review wording about pending commits describes the earlier
checkpoint. No push, production migration, real collection import or deployment
was performed. Product code is committed; task archive/journal bookkeeping follows.

## Commit-preparation follow-up

The user replied “继续” to the presented three-group local commit plan. Chat and
SSO were prepared as independently tested cumulative snapshots; their index
blobs were separately reviewed before each commit. Chat frontend 176 tests and
SSO frontend 205 tests passed typecheck/lint/build; focused backend sets passed
9 and 169 tests respectively. No production services or original source files
were used as scratch staging copies.

The environment's Starlette was aligned from 1.8.0 to its 0.52.1 lock entry using
the exact wheel hash, without changing other distributions; 123 focused API
tests passed. This is not a claim that the whole environment matches uv.lock:
Pydantic and AnyIO still satisfy project requirements at other versions. A
separate remaining 422 documentation difference came from Python's standard
HTTP phrase, not the Starlette version. CI runs Python 3.11 and local Python is
3.13. The exporter now normalizes only the two standard 422 descriptions to the
existing contract, preserving custom descriptions and schemas. Two real-export
regressions pass in each snapshot; all three canonical JSON/TypeScript checks
pass. Chat generated files remain byte-identical to the previous HEAD.

The final follow-up full backend run after this narrow fix is **1194 passed,
104 skipped, 2 existing date-dependent failures**. Compared with the earlier
1216/74 run below, the temporary PostgreSQL server is now stopped: 14 multipart
and 10 external-login integration cases skip, plus six newly added Redis tests
need their opt-in binary. Two exporter tests are newly passing. Earlier real
PostgreSQL/Redis evidence remains separate, not added as a combined count.
See artifacts/commit-backend.txt and .xml. Raw logs remain outside Git; committed
copies replace personal host/home paths, preserving counts, with hashes in
artifacts/log-redactions.json.

Before staging, all 1116 paths in this turn's preservation snapshot remained
present. Only the expected exporter/spec/generated files, approval note and
redacted logs changed; original product source was otherwise byte-identical.
The older lost pre-implementation snapshot limitation below is unchanged.

## Delivery

Implemented public favorites preview/selection and independent verified Bilibili
parts. A selected part remains one ordinary saved/searchable item, with its own
transcript/time axis, lifecycle and accurate owned source action. The ten-entry
action bound, configured ingestion quotas and worker concurrency are unchanged.
Ordinary BV/share input, prior SSO and chat/history changes remain in the worktree.

Public discovery is read-only, bounded and credential-free. Parts import requires
explicit selection, signed tenant-bound references and normal admission. Legacy
unknown P1 provenance stays unknown, with default-off per-entry overlap consent.
At the pre-commit checkpoint, no real sample import, production/shared migration,
deployment or Git commit had run. Local commits are now recorded above.

## Automated and independent evidence

| Gate | Result |
| --- | --- |
| Final full backend after provider-rate fix | 1216 passed, 74 skipped, 2 existing date-dependent failures; artifacts/combined-backend-final.txt and .xml |
| Final full frontend after native-QA layout fix | 256 passed across 24 files; typecheck, lint, build and check:api passed |
| Independent favorites/backend review | 99 focused tests passed; strict parsing/transport/CSRF/ref/expiry reviewed |
| Independent final frontend review | 67 focused tests and check:api passed, including the layout fix |
| Independent extractor/worker/retrieval review | 111 passed after the known-CID legacy retry correction |
| Real isolated PostgreSQL | 14 passed independently as well as by implementation owner |
| Real isolated Redis | 6 passed; separate explicit-binary opt-in run, not part of the preceding full-backend count |
| Schema and whitespace | One Alembic head f8a9b0c1d2e3; git diff --check passed |

Test sets overlap and are not added into a single total. The PostgreSQL test
runtime had pg_trgm, without vector. The new additive migration was checked
against an isolated miniature predecessor schema; this does not claim the entire
historical production migration chain ran. The Redis tests launched only their
own PID-checked random-loopback instances with persistence disabled; all stopped.

The only full-backend failures are:

- tests/test_item_management_tools.py::test_save_restore_early_return_is_committed_and_retry_queue_failure_is_retryable
- tests/test_item_management_tools.py::test_delete_restore_and_resave_converge_on_same_item

Both August-seeded deletion fixtures are now past the retention window and
produce purge_in_progress rather than restored. They were observed before this
feature. No unrelated fixture dates or retention behavior were changed to hide
them. Conditional skips are recorded in the XML, not represented as passes.

Full backend used an unavailable default database/Redis, inert provider keys and
only the explicit temporary PostgreSQL test URL. It did not use production data
or model services. Temporary PostgreSQL port 55439 is now stopped.

## Provider qualification

- Actual favorites client read the supplied public study collection: eight
  reported/returned/available parent entries, page 1/20, no further page. See
  research/favorites-canary-20261016.json.
- Bounded parts lookup read BV1gA411j7Ta with 46 parts. See
  research/parts-canary-20261016.json.
- The actual controlled extractor verified P2/CID1432055578, with matching
  actual subtitle-hook evidence and unchanged pre/post source mapping. Its
  observed result was NeedsASR with zero cues, not a successful caption import.
  See research/controlled-part-canary-20261016.json.

These were metadata/subtitle-read-only calls, without credentials, media/files,
items, queues or actual ingestion. They qualify these observed paths, not all
provider availability, subtitles or production network access.

## Browser acceptance

The root session obtained IAB access after earlier locked-device attempts.
It tested the real frontend against the explicitly labelled RAM-only fixture,
on separate ports 8021/5181. See research/browser-acceptance.md and screenshots.
Native modal/Escape, keyboard selection, desktop/mobile geometry, lazy expansion,
cross-page selection/dedupe, atomic ten-entry cap, legacy consent, partial results,
unknown same-operation replay and source clicking passed.

A real browser found the selection count disappeared while scrolling a long
list. The resulting narrow sticky toolbar and confirmation shortcut were fixed,
independently reviewed and rechecked in both sizes. Source action displayed the
expected current P6 and local t185 in the fixture, without contacting Bilibili.

Web Storage objects were not exposed by the available read-only browser evaluator.
Actual storage enumeration is therefore not claimed. The no-persistence boundary
is supported by source review and lifecycle/component tests. Native platform
login and production authentication/deployment were outside this fixture check.

Both QA tabs closed, browser viewport reset, and only the original user tab at
5173/login remained. Both QA services stopped; ports 8021/5181 are free. No
original demo process was stopped. A final listener check found 5173/8000 also
not running; a stale existing browser URL is not evidence of a live app.

## Review and task boundary

Both child independent reports pass product code/protocol review. Executable
backend/frontend specs and user documentation were updated. Parent AC1–10 map
to the child review's cross-child evidence table and the native findings above.

The initial temporary baseline snapshot no longer exists. No restore/reset was
used; review used actual diffs, ownership and regression evidence. Bytewise
baseline preservation is not claimed. Existing shared files contain earlier
SSO/chat/BV work, and the new migration follows the SSO predecessor, now committed as 2bb6dbc.
No blanket staging is safe. The scoped commit plan received the required human confirmation (“继续”);
all three local work commits and independent index checks are recorded above.
