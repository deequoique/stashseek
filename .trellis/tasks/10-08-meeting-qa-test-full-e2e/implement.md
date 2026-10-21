# Implement: RQ1 on the full test split

Implementer: `trellis-implement` sub-agent (model **sonnet**). Checker: `trellis-check`.

## Safety (every step)

- Never use the production `.env` `DATABASE_URL`.
  - pytest goes through `data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh`.
  - Runs go through `production_path.prepare_test_environment()`, which asserts
    the test-branch host.
  - Never print DSNs.
- Do not modify `app/`, and do not modify the 09-04 copies (`.runtime/worktrees/agent-benchmark`, `~/notebook-agent-bench`).
- No git commit; `evals/meeting_gold` stays uncommitted WIP of task 09-12.
- Real runs make embedding calls only. No chat-model calls.

## Steps

1. Diff `app/ingest/embed.py` / `app/bootstrap.py` between dev and
   `origin/main` for the text-embedding path that `build_embedding_provider`
   and `build_worker_embedder` use. Record the finding in
   `research/run-environment.md`; see design §4 for the fallback.
2. Code changes per design §2. Add tests in `tests/test_meeting_gold_rq1_*.py`:
   - `split_meeting_ids` for val and test;
   - `--selection` / `--split` resolution, including the mismatched split or
     hash rejection and mutual exclusion with `--case-id`;
   - pool metrics on a synthetic pool;
   - the meeting-localization fields;
   - summaries grouped by kind, with collision exclusion.
3. Run the `evals/meeting_gold` tests through `run_tests_testdb.sh`; all must pass.
4. **AC4 reproduction**: `rq1 --split val` (default 30 cases) into
   `data/meeting_gold/runs/rq1-val-repro-<date>/`. Reuse the 10-06 library
   only if its fingerprint matches; otherwise re-ingesting is fine. Baseline
   Recall@5 must equal 0.381 (A) and 0.090 (B) on n=28.
5. Selection: `select --cases data/meeting_gold/final --split test --all --seed 0 --output data/meeting_gold/runs/rq1-test-<date>/selection.json`.
6. **Real run**: `rq1 --split test --selection …/selection.json --output data/meeting_gold/runs/rq1-test-<date>`.
   On an I/O or network error, rerun the same command; the run resumes.
7. Spot-check 5 per-case records by hand: the gold mapping, pool recall, and
   the meeting-localization fields.
8. Write `research/report-rq1-test.md` (Chinese):
   - the PPT summary table;
   - scope A vs B with the localization breakdown;
   - the ablation table with McNemar / bootstrap;
   - side-by-side with the val RQ1;
   - the note that the PPT's Agent end-to-end numbers use a different basis;
   - exclusions.
9. `trellis-check`, spec update if any contract changed (likely none outside
   `evals/`), then stop and report to the user.

## Validation commands

```bash
data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh tests/test_meeting_gold_rq1_*.py tests/test_meeting_gold.py
.venv/bin/python -m evals.meeting_gold rq1 --split val --output data/meeting_gold/runs/rq1-val-repro-<date>
.venv/bin/python -m evals.meeting_gold rq1 --split test --selection data/meeting_gold/runs/rq1-test-<date>/selection.json --output data/meeting_gold/runs/rq1-test-<date>
```
