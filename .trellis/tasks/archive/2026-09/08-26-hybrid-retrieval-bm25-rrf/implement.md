# Implementation Plan

## Gate 0 — Confirm deployment boundary

- [ ] Confirm the recommended support matrix: Neon production uses
      `lakebase_bm25`; local/ordinary PostgreSQL uses explicit `postgres_fts`.
- [ ] Record the selected decision in PRD/design and remove the open-decision
      gate before `task.py start`.

Rollback gate: planning only; no code or database state exists yet.

## Phase 1 — Freeze baseline and retrieval evaluation

- [ ] Add strict, versioned retrieval dataset/schema using stable public fixture
      identities; reuse existing Gold Evidence definitions where compatible.
- [ ] Add direct retrieval variant runner and pure metric aggregation for
      Recall@1/3/10, MRR, no-evidence false positives and item coverage.
- [ ] Add subgroup coverage for exact lexical, code/proper-name, CJK,
      code-switch, cross-language, semantic paraphrase and no-evidence.
- [ ] Record a sanitized legacy report before changing runtime ranking.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_natural_language_quality.py
.venv/bin/python -m evals.retrieval --validate-dataset
.venv/bin/python -m evals.retrieval --variant legacy --validate-only
```

Rollback gate: evaluator is opt-in and not imported by runtime.

## Phase 2 — Accurate contracts and RRF

- [ ] Replace misleading `bm25_search` naming with a lexical backend contract
      and provenance-aware ranked hits; update CLI/docs/tests.
- [ ] Implement deterministic RRF, duplicate contribution, stable tie-breaks and
      fused-score hydration.
- [ ] Move item diversification after RRF while preserving all existing caps.
- [ ] Keep `RETRIEVAL_FUSION_MODE=legacy` during the first deploy.
- [ ] Benchmark a small predeclared RRF parameter grid on the tuning split;
      freeze the selected constant and prove it on holdout.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_knowledge_services.py \
  tests/test_multiuser_integration.py tests/test_exact_video_reference_routing.py
.venv/bin/python -m evals.retrieval --variant postgres-fts \
  --variant vector --variant rrf
```

Rollback gate: select legacy fusion; no schema rollback required.

## Phase 3 — Multilingual lexical documents and backfill

- [ ] Specify and implement bounded deterministic English/CJK/code tokenization
      shared by ingestion, query and backfill.
- [ ] Add schema/version marker and Alembic migration without creating a second
      head; keep existing data readable during rollout.
- [ ] Add resumable bounded backfill with counters/checkpoints and safe failure
      behavior; never log lexical content.
- [ ] Make new ingestion/retry write the new lexical document and verify parity
      with backfill fixtures.
- [ ] Run backfill on a disposable/local database and an isolated Neon branch.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_tasks.py tests/test_knowledge_services.py
alembic heads
alembic upgrade head
alembic current
alembic check
```

Rollback gate: application continues reading the old lexical path; retain new
column/data for forward retry rather than destructive downgrade.

## Phase 4 — Neon `lakebase_text` backend

- [ ] Add migration/admission steps for `lakebase_text` and `lakebase_bm25`
      index using the direct migration URL only.
- [ ] Add explicit backend configuration and readiness checks for extension,
      index, lexical-document version and one Alembic head.
- [ ] Implement parameterized tenant/item/reference-scoped BM25 SQL with a
      constant server-owned index identifier.
- [ ] Verify `EXPLAIN`, top-K, strict filters, backfill/VACUUM behavior and query
      failure mapping on an isolated Neon branch.
- [ ] Keep ordinary PostgreSQL fallback tests independent of Neon-only tests.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_configuration.py \
  tests/test_deployment_cli.py tests/test_knowledge_services.py
# Isolated Neon branch only:
alembic upgrade head
alembic current
alembic check
.venv/bin/python -m evals.retrieval --backend lakebase_bm25 --holdout
```

Rollback gate: set `LEXICAL_SEARCH_BACKEND=postgres_fts`; leave BM25 index in
place until a separate cleanup.

## Phase 5 — Integration, rollout and documentation

- [ ] Run retrieval holdout and compare all PRD quality gates against the frozen
      legacy baseline; do not switch defaults on a failed gate.
- [ ] Re-run Agent convergence, exact/item scope, deletion/archive, multi-user,
      diagnostics, Composer and natural-language evaluation regression suites.
- [ ] Update configuration reference, ingestion/retrieval explanation, runtime
      profile/deployment/rollback guides and backend support matrix.
- [ ] Deploy with old backend/fusion, verify readiness, then switch RRF and BM25
      separately; capture only privacy-safe aggregate diagnostics.
- [ ] Update `.trellis/spec/backend/agent-retrieval-convergence.md` and database
      guidance with the actual accepted backend/fusion/migration contracts.

Validation:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m evals.natural_language --validate-catalog
.venv/bin/python -m evals.natural_language --validate-human-samples \
  --require-complete-human-samples \
  --human-samples .trellis/tasks/archive/2026-08/08-18-agent-quality-benchmark/human_eval_samples.yaml
.venv/bin/python -m evals.retrieval --backend lakebase_bm25 --holdout
```

Rollback gate: restore the previous backend/fusion environment values and
restart admission; no user data or index deletion is part of emergency rollback.
