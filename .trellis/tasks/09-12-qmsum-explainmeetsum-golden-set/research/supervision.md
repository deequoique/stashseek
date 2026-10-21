# Implementation Supervision

## Resumption

The user explicitly resumed delegated implementation after document review.
Implementation worker owns `evals/meeting_gold/`, focused tests and local source/
build artifacts. Supervisor owns task contract clarification and final acceptance.
No unrelated runtime changes, commits, pushes or paid calls are authorized here.

## Existing Evaluation Baseline

Command run by supervisor before integrating the resumed implementation:

```sh
.venv/bin/python -m pytest tests/test_natural_language_quality.py tests/test_natural_language_evaluator.py tests/test_video_recognition_benchmark.py tests/test_video_recognition_pilot.py tests/test_video_recognition_low_human.py -q
```

Result: 104 passed, 1 failed. Existing
`test_preflight_refuses_production_before_opening_database` fails while creating
production Settings because the current browser-companion wildcard origin requires
a loopback-only development web server (`app/config.py:809`). These files already
have unrelated worktree changes; the meeting dataset task must not modify them
to mask this failure. Re-run the same baseline at final verification.

Confirmed environment isolation: prefixing the same command with
`env BROWSER_COMPANION_ALLOWED_ORIGINS=` yields **105 passed** in 7.05 seconds.
No configuration file was modified; this override applies only to the test
process. Use this explicit environment for the final comparable regression run.

## Initial Draft Review

The paused worker's draft did not parse the real published source shape: QMSum
does not supply sentence-level content, ExplainMeetSum query lists are nested
under `explainable_qmsum`, and annotation groups contain nested `evidence` with
`sent_index`. The draft's synthetic tests also modeled a different structure.
Supervisor rejected that draft as acceptance evidence and directed real-source
audit, strict parsing and source-shaped regression fixtures before proceeding.

## Gates

- Full pinned source audit and reconciliation policy reviewed before freeze.
- Exact state/count/exception baseline reviewed before dataset acceptance.
- Source and artifact integrity validated independently of successful build.
- No fabricated timestamps or semantic citation completeness labels.
- Independent reviewer plus supervisor test/build verification before completion.

## First-Slice Checkpoint (2026-09-13)

The replacement worker returned real-format loader changes; supervisor re-ran
`pytest tests/test_meeting_gold.py -q`: 15 passed. This validates only the first
slice, not the old build/score CLI, which remains rejected until replaced.

Worker-reported full-source audit (awaiting persisted detailed exception report):

- 232 meetings, 1,810 QMSum cases; 1,759 exact joins and 51 answer-only cases.
- CES: 1,720 scorable, 39 partial, 51 answer-only, 0 quarantined.
- CES+PES: 1,746 scorable, 13 partial, 51 answer-only, 0 quarantined.
- Unmatched QMSum: 44 whitespace-only answer differences and 7 missing covid_1
  cases. Exact matching remains the policy; no normalization was approved.
- All inspected SummaryEvidence index unions match contiguous 0..N-1 and EMS.
- No raw-transcript or exact query-kind/query/answer cross-split collisions;
  two cross-split answer-hash collisions require exact IDs before acceptance.

Supervisor requested loader fixes before acceptance: remove pseudo sentence IDs
for QMSum-only/mismatched transcripts, preserve evidence ID/type length alignment
even on invalid input, prevent quarantine downgrade, validate span bounds and
empty queries/answers, and add hermetic source-shaped fixtures.

Next-stage write scopes are disjoint: original worker owns loader fixes; scorer
worker owns scoring.py and its tests; artifact worker owns artifacts.py, CLI,
source lock, notices, README and artifact tests. Supervisor retains freeze approval.

## Loader Fix Review

Worker returned fixes and a structured `first-slice-audit.json`, reporting 25
passing tests. Supervisor reviewed the corrected empty sentence views, paired
ID/type projection, quarantine-preserving setters and raw annotation retention.
Counts remain unchanged. Baseline numeric counts are provisionally approved for
the exact-match policy; no whitespace normalization is approved.

Before accepted freeze, resolve covid_1's missing domain through unique pinned
QMSum domain-file membership and matching source bytes, not its filename prefix.
This moves seven train cases from unknown to Committee without changing totals.
Record both exact cross-split answer collisions: train Bed005 general0 / val
Bed010 general0, and val Bed010 specific2 / test Bmr023 specific2. The second
blocks held-out eligibility for that test case. Original splits remain unchanged.

## Independent Verification And Second Review

Supervisor independently ran the loader and scorer tests: **40 passed in 11.50s**.
The comparable existing evaluation regression command with the explicit
`BROWSER_COMPANION_ALLOWED_ORIGINS=` process override passed **105 tests in
10.67s**. No application configuration was changed.

Independent review nevertheless found five scorer contract gaps: the returned
eligibility state used CES even for CES+PES runs; malformed answer index/text
mappings could score; contradictory duplicate evidence labels were accepted;
cross-policy quarantine inconsistency could bypass exclusion; and stable IDs
were not checked against their coordinate fields. These are acceptance blockers,
delegated to the scorer worker with focused regression tests required.

The artifact worker is still completing build/validate/select/CLI and integrity
tests. A source lock now exists but remains explicitly unaccepted. The reviewer
has moved to artifact integrity review while scorer repairs run. An existing file
or successful subagent dispatch is not evidence that a dataset has been accepted.

## Exact Baseline Approval

After the artifact worker returned, the supervisor independently recalculated
the exception inventory directly from the pinned source JSON, without using
the artifact builder's exception derivation. Exact query/answer joins,
answer-only IDs, CES and CES+PES partial IDs, missing meetings, unused EMS IDs,
and both cross-split answer collisions match the current lock exactly.
The canonical exception-inventory SHA-256 is
`9e34367d4ec5cbd1f90b0ea5af80ce0ebcbb44987dc93bead0313150f33a22be`.
The current exact baseline is now supervisor-approved. This approval does not
constitute acceptance of the converter or scorer implementation.

The supervisor re-ran loader and artifact tests: **36 passed in 0.53s**.
Scoring repairs remain under separate delegated ownership. Follow-up CLI review
requires nonzero exit for incomplete runs, duplicate JSON-key rejection,
full-split selection, and examples that keep selection/report files outside
the fixed artifact payload inventory. A fresh full-source build is in progress
under ignored `data/meeting_gold/frozen`.
