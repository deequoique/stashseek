# Implement: RQ1 study

Implementer: `trellis-implement` sub-agent, model **sonnet**. Checker:
`trellis-check`.

**Precondition:** `10-06-fix-chunk-merge` is implemented and checked in the
working tree. Verify with `tests/test_chunker.py` passing on the new contract
before building the library.

## Ordered checklist

1. `evals/meeting_gold/production_path.py`:
   - caption-track builder with sentence provenance
   - golden connector and memory store
   - library ingest with test-host assertion
   - segment→cue mapping
   - full/partial containment
   - idempotent manifest

   Port from the session scratch runner `meeting_agent_prodpath.py`; keep its
   assertions.
2. `evals/meeting_gold/stats.py` with unit tests on fixed inputs (McNemar exact
   values, Wilson bounds, bootstrap determinism with the seed).
3. `evals/meeting_gold/retrieval_study.py`:
   - lexical OR query
   - fusion methods
   - diversify on/off via production `_diversify_hits`
   - scope A/B
   - R2 equivalence and scope-A invariance assertions
   - EXPLAIN capture
   - exact-dense diagnostic
   - metrics, ceiling

   Unit tests use toy rankings and a toy ceiling.
4. `evals/meeting_gold/report.py`: tables (MD + CSV), `attribution.svg`,
   failure cases per the rule.
5. `evals/meeting_gold/agent_smoke.py` (R8): port the scratch production-path
   agent runner, including the instrumentation of stream failure categories
   (no model prose persisted) and one process per case.
6. `evals/meeting_gold/__main__.py`: subcommands `rq1` and `agent-smoke`. Update
   `evals/meeting_gold/README.md` with usage, protocol and safety notes.
7. Run the study once on the post-fix chunker and write the artifacts to
   `data/meeting_gold/runs/rq1-<date>/`. Hand back a summary for the deck.

## Validation

```bash
.venv/bin/python -m pytest -q tests/test_meeting_gold*.py
.venv/bin/python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/rq1-$(date +%Y%m%d)
.venv/bin/python -m pytest -q
```

`rq1` must exit non-zero if any R2 or scope-A invariance assertion fails.

## Risks / rollback

- Additive only: new eval modules and CLI subcommands. Rollback = delete the
  modules and subcommands.
- DB: writes only to a fresh test-branch tenant. The test-host assertion must
  refuse the production DSN.
