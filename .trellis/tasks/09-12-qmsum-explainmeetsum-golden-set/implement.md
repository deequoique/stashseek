# Implementation Plan

Status: implementation approved; continue only within the reviewed scope.

## Ordered Work

1. Re-read this task and current worktree status; load backend/cross-layer specs.
   Confirm source commits/licenses and perform a complete local source audit.
   Record query/annotation counts, empty groups, label types, duplicates, answer
   segmentation anomalies and transcript differences before finalizing the lock.
   Freeze the eligibility state table, one-to-one consumption rules, prediction
   completeness modes, and the non-circular dataset-hash file list in the manifest.
2. Implement `evals/meeting_gold/` strict schema and deterministic source loader.
   Prefer local checkout inputs and a separately invoked pinned-source fetch
   helper. Commit source lock/notices; keep full downloads/builds ignored.
3. Implement audited matching, sentence-pointer validation, eligibility views,
   split preservation, and corpus/answer-key separation. Never import copies
   from per-domain folders or generated model/oracle output.
4. Implement atomic deterministic build, validate, select and score commands,
   using `python -m evals.meeting_gold` as a separate CLI. Document exact flags
   when implemented; they are not existing commands yet.
5. Produce a real full build plus deterministic val smoke selection locally.
   Review every exclusion category and counts with the supervisor; freeze an exact
   count/exception baseline before publishing an accepted dataset. Any subsequent
   drift needs a reviewed revision, not an undocumented normalizer/fuzzy match.
6. Add focused synthetic fixtures and minimal attributed upstream regression
   fixtures for all three domains and both query types. Validate the entire
   frozen corpus outside the ordinary network-free unit-test suite.
7. Review all changes for data leakage, evidence semantics, reproducibility,
   and regressions. Update the backend spec with established conventions only
   after implementation verifies them. Present the scoped commit plan for
   approval; do not commit, push, or archive automatically.

## Test Matrix

- Correct per-meeting joins, same question in multiple meetings, reordering,
  duplicate queries, answer mismatches, missing meeting and unexpected extras;
  assert one-to-one consumption and audit all unused source records.
- Index bounds/types, repeated sentences, speaker/text mismatch, merged answer
  groups, empty/partial evidence, unknown types, contradictory duplicate labels.
- No fabricated seconds or negative cases; no limits inherited from video Gold.
- Split preservation, cross-split duplicate meeting IDs fail structurally; identical
  transcript hashes under different IDs remain in base artifacts and block held-out
  certification when touching test. No test sampling in val smoke; deterministic
  IDs/ordering/selection and file hashes. Never silently delete official duplicates.
- Mutation, wrong revision, stale prediction hash, unknown IDs, interrupted
  writes and overwrite refusal; bounded source acquisition and no upstream code.
- CES-only/CES+PES policy separation, duplicate citations, other-meeting misses,
  empty citations, no eligible groups, missing cases and macro denominators;
  exact selection/hash binding, incomplete mode, and base-hash independence from
  selection artifacts.
- Exact answer consumption: omitted first/last/interior text, reordered/overlapping
  indices, repeated answer text, merged groups, and versioned reconciliation rules.
- Audit baseline drift, stable exception IDs, zero eligible domain, and strict
  per-policy states. Complete/incomplete/partial run semantics, duplicate prediction
  records, explicit empty arrays, empty selections and policy-specific denominators.
- Corpus cannot contain answer-key fields; logs/errors omit full source content;
  no provider/database/network access during offline unit tests.
- Existing `evals/natural_language` Gold scoring/CLI and video evaluation tests
  retain their contracts. Unrelated dirty files remain untouched.

## Validation Commands

Current planning validation:

```sh
python3 .trellis/scripts/task.py validate .trellis/tasks/09-12-qmsum-explainmeetsum-golden-set
git diff --check
```

Implementation commands (to add and verify):

```sh
.venv/bin/python -m pytest tests/test_meeting_gold.py
.venv/bin/python -m evals.meeting_gold --help
.venv/bin/python -m compileall -q evals/meeting_gold
```

Discover existing evaluation test filenames before running that regression
selection. Inspect project tooling before adding lint/type-check commands;
`pyproject.toml` currently declares pytest but no lint/type-check configuration.
Document unavailable tooling and pre-existing failures separately from task results.

## Delivery And Stop Conditions

Deliver the local artifact paths, exact split/query/eligible counts, source and
dataset hashes, exception summary, and tested commands. State clearly that a
dataset build is not an executed live Agent benchmark.

Stop for review if source license/structure is incompatible, unexpected evidence
types occur, systematic mismatches need lossy normalization, or live ingestion/
runtime changes are necessary. None permits silently relaxing old Gold gates.
