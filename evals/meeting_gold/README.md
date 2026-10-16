# Meeting Golden Set

Offline, citation-aware reference data for QMSum enriched by ExplainMeetSum.
QMSum is authoritative for questions, answers, raw turns, and official
train/val/test membership. ExplainMeetSum contributes sentence evidence only
when the exact question-and-answer tuple matches.

## Build And Validate

Use clean checkouts at the commits recorded in `source_lock.json`. The command
verifies repository HEADs, clean status, tracked bytes, canonical split files,
QMSum domain membership, and upstream license/README hashes before publishing:

```sh
python -m evals.meeting_gold build \
  --qmsum /path/QMSum/data/ALL \
  --explainmeetsum /path/ExplainMeetSum/data/ExplainMeetSum \
  --output data/meeting_gold/frozen
```

The output is published atomically only after byte-level validation. Rebuilding
the same locked inputs is idempotent; an existing output for a different
dataset hash is rejected. Validate artifact bytes alone with:

```sh
python -m evals.meeting_gold validate --output data/meeting_gold/frozen
```

To validate the source checkouts again in the same command, add both
`--qmsum` and `--explainmeetsum` paths. A source lock must contain the accepted
freeze baseline; candidate or incomplete locks are rejected.

The payload separates `corpus.jsonl` (meeting turns and published sentence
coordinates, with no answers or evidence labels) from the split answer-key
files. `audit.json` records source accounting, exact unmatched IDs, policy
eligibility, partial cases, and leakage collisions. `manifest.json` binds the
payload hashes, source lock, accepted baseline, and dataset hash.

The reviewed first freeze contains 232 meetings and 1,810 QMSum cases: 1,759
exact joins and 51 answer-only cases. CES eligibility is 1,720 scorable and 39
partial; CES+PES eligibility is 1,746 scorable and 13 partial. These numbers
are checked against the accepted lock, not used to truncate or synthesize data.

## Deterministic Selection

Selection reads the validated artifact directory, defaults to a val-only
30-case smoke view, and records deterministic IDs, group shortfalls, and
held-out leakage status. Keep selection files under the sibling `runs`
directory: artifact validation uses a fixed file inventory.

```sh
python -m evals.meeting_gold select \
  --cases data/meeting_gold/frozen \
  --split val \
  --limit 30 \
  --seed 0 \
  --output data/meeting_gold/runs/val-smoke-selection.json
```

For a full-split scorer run, use `--all`; the smoke cap does not apply:

```sh
python -m evals.meeting_gold select \
  --cases data/meeting_gold/frozen \
  --split test \
  --all \
  --seed 0 \
  --output data/meeting_gold/runs/test-all-selection.json
```

Test selections that touch an exact train/val collision are marked
`held_out_eligible: false` with the collision records retained. Validation
rejects stale dataset hashes, duplicate or cross-split IDs, altered shortfall
or leakage diagnostics, and invalid selection hashes.

## Scoring

The offline scorer accepts hash-bound prediction records with ranked retrieved
and cited sentence IDs. Use `--policy ces` or `--policy ces_pes`; use
`--mode partial` only for an explicitly incomplete subset. The reported
overlap metrics are annotation-coverage diagnostics, not semantic support or
generated-answer citation completeness.

```sh
python -m evals.meeting_gold score \
  --cases data/meeting_gold/frozen \
  --selection data/meeting_gold/runs/val-smoke-selection.json \
  --predictions data/meeting_gold/runs/val-predictions.json \
  --output data/meeting_gold/runs/val-report.json
```

`predictions.json` is an exact envelope with no generated answer field:

```json
{
  "schema_version": "meeting-gold-predictions-v1",
  "dataset_hash": "<dataset-hash>",
  "selection_hash": "<selection-hash>",
  "records": [
    {
      "case_id": "<selected-case-id>",
      "retrieved_sentence_ids": ["<sentence-id>"],
      "cited_sentence_ids": ["<sentence-id>"]
    }
  ]
}
```

Complete scoring returns a nonzero exit status when any selected prediction is
missing. Use `--mode partial` only when that incomplete subset is intentional.

Cases without eligible evidence remain unavailable and are retained in the
omission counts. No timestamps, translations, generated reference claims, or
provider calls are introduced. The scorer evaluates sentence-ID retrieval and
citation overlap only; semantic reference-answer evaluation, a generated-answer
judge, and an actual Agent adapter are out of scope. The CLI report carries the
validated selection diagnostics, including `held_out_eligible` and collision
reasons.

## RQ1: Production-Faithful Retrieval Study

`rq1` answers "how well does hybrid lexical-dense retrieval recover annotated
evidence in long spoken transcripts, and how much do fusion and
diversification choices matter?" using, by default, the fixed 30 specific-query
cases (10 per domain) from `evals/meeting_gold/retrieval_study.py:RQ1_CASE_IDS`.

```sh
.venv/bin/python -m evals.meeting_gold rq1 \
  --output data/meeting_gold/runs/rq1-<date>
```

The same study runs on the whole test split (every meeting of the split goes
into one tenant, so scope B has all of that split's distractors):

```sh
.venv/bin/python -m evals.meeting_gold select --cases data/meeting_gold/final \
  --split test --all --seed 0 --output data/meeting_gold/runs/rq1-test-<date>/selection.json
.venv/bin/python -m evals.meeting_gold rq1 --split test \
  --selection data/meeting_gold/runs/rq1-test-<date>/selection.json \
  --output data/meeting_gold/runs/rq1-test-<date>
```

`--split {val,test}` (default `val`) picks the library and requires every case
to belong to it. `--selection` (validated against the artifact bundle; its
split must equal `--split`) and `--case-id` are mutually exclusive; without
either, `val` runs the fixed 30 cases and `test` is refused. Test cases that
duplicate a train/val record (the selection's `held_out_reasons`) are still
retrieved and written to `per_case.jsonl` but excluded from every summary and
comparison. An output directory holding a library of other meetings is refused,
because scope B searches the whole tenant.

What this does, end to end, on the Neon **test** branch only:

1. Resolves `DATABASE_URL` from `.env.neon-test` (never `.env`), asserting
   the test host and that it differs from production, before any database
   connection (`production_path.prepare_test_environment`).
2. Ingests every meeting of the split (for the default val run: 35 meetings,
   20 touched by the 30 cases, 15 distractors) into **one** tenant through the real `create_item` +
   `process_item` worker path — production chunking, embedding, and FTS —
   with the transcript synthesized as a YouTube json3 caption track. The
   build is idempotent and checkpointed per meeting
   (`<output>/library.json`): a retry reuses meetings already ingested.
3. For each case, in scope B (tenant-wide) and scope A (`item_id`-scoped),
   fetches production-sized candidate pools (lexical AND, lexical OR, dense)
   and evaluates the full settings matrix: lexical-only, dense-only,
   raw-score merge (production), RRF, and min-max fusion, each with
   diversification on/off.
4. Asserts production equivalence (R2): the raw-score-merge/AND/diversify-on
   setting must exactly match `KnowledgeServices.search_segments`, per case
   and scope. Asserts scope-A diversify on/off invariance. The command exits
   non-zero if either assertion fails anywhere.
5. Computes Hit/Recall/MRR/nDCG@k (full chunk containment of gold sentences),
   the exact chunking ceiling, an ANN vs exact-dense diagnostic (R7), and
   paired statistics (exact McNemar, Wilson, bootstrap) against the
   production baseline.
6. Also records, per case, the candidate-pool coverage (dense top-50 U
   lexical top-50, AND and OR variants: pool size, hit, recall before any
   ranking) and, in scope B, meeting localization (is the first result the
   case's own meeting, and if not, is the mistaken meeting from the same
   source domain).
7. Writes `setup.md`, `baseline.md`/`.csv`, `ablation.md`/`.csv` (plus
   `ablation_specific` / `ablation_general` with the same paired comparisons
   per query kind), `attribution.svg`/`.csv`, `failure_cases.md`,
   `summary_by_group.md`/`.csv` (production setting by query kind and
   domain), `summary_ppt.md` (scope x query kind tables), `per_case.jsonl`,
   and `run_manifest.json` to the output directory.

No provider calls happen beyond query/ingest embedding — `rq1` never calls an
LLM. Every database session used by the study opens a read-only transaction.

## R8: Production-Path Agent Smoke

`agent-smoke` is the end-to-end harness the parent task's post-fix rerun
uses: real `agent.stream()` calls, a fresh thread per case, one subprocess
per case, and instrumentation of the stream failure taxonomy (whitespace
stream-guard aborts, stream-plan over-citation, composer-fallback
fail-closed) without ever persisting model prose.

```sh
.venv/bin/python -m evals.meeting_gold agent-smoke \
  --output data/meeting_gold/runs/rq1-<date> \
  --dry-run   # validates ingestion reuse and request wiring; no model call
```

Pointed at the same `--output` directory as a completed `rq1` run,
`agent-smoke` reuses that run's library (`library.json`) instead of
re-ingesting. Dropping `--dry-run` performs real model calls and must only be
run deliberately. `evals/meeting_gold/agent_smoke.py:categorize_failure` is a
pure, offline-testable function reproducing the 2026-10-06 pre-fix failure
breakdown recorded in
`tests/fixtures/meeting_gold/prodpath-rerun-20261006.cases.json` (copy of the archived task research file).

## Attribution

Both upstream projects are MIT-licensed. Retain `NOTICE.md`, `QMSUM_LICENSE`,
and `EXPLAINMEETSUM_LICENSE` with redistributed artifacts. Cite:

- Zhong et al., “QMSum: A New Benchmark for Query-based Multi-domain Meeting
  Summarization,” NAACL 2021, https://arxiv.org/abs/2104.05938.
- Kim et al., “ExplainMeetSum: A Dataset for Explainable Meeting Summarization
  Aligned with Human Intent,” ACL 2023, https://aclanthology.org/2023.acl-long.731/.
