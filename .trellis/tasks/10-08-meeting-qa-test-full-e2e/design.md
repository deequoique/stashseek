# Design: RQ1 retrieval study on the full test split

## 1. Boundaries

- Only `evals/meeting_gold/` (and its tests) change. `app/` is untouched.
- `evals/meeting_gold/` is uncommitted WIP owned by task 09-12. Its canonical
  copy is the dev working tree. Task 09-04 keeps identical copies of
  `retrieval_study.py` and `production_path.py` in
  `.runtime/worktrees/agent-benchmark` and `~/notebook-agent-bench`, but adds
  its own `benchmark` subcommands to `__main__.py`.
  - This task edits only the dev copy.
  - The `__main__.py` change stays limited to the `rq1` parser, so a later merge
    with 09-04's copy is a small hunk.
- No chat-model calls. Embedding calls only: 35 meetings at ingest, plus 281
  query vectors cached in `query_vectors.json`.

## 2. Code changes

| Area | Change |
|---|---|
| `production_path.val_meeting_ids` | Generalize to `split_meeting_ids(bundle, split)`; keep `val_meeting_ids` as a thin wrapper so existing callers and tests stay valid. |
| `retrieval_study.run_rq1` | New `split` parameter, default `"val"`. Its library is `split_meeting_ids(bundle, split)`. Fail with `StudyError` if any case's meeting is not in the library or the split. |
| `__main__ rq1` | New `--split {val,test}` (default `val`) and `--selection <file>`. The case list comes from `--case-id`, else the selection's `selected_case_ids` (validated with `validate_selection`; split and dataset hash must match), else `RQ1_CASE_IDS`. `--selection` and `--case-id` are mutually exclusive. |
| per-case record | Add `query_kind`, `ces_status` and `pool` metrics. For each scope and lexical variant: `pool_size` (\|dense ∪ lex_variant\|), `pool_hit` and `pool_recall` (strict containment over that union). `pool_and` is the production pool. |
| meeting localization (scope B) | `top1_meeting_correct`, `meeting_hit@5`, `wrong_top1_meeting_id`, and `wrong_top1_same_source`. Source groups: AMI = ES/IS/TS, ICSI = Bed/Bmr/Bro, Committee = `education_*` / `covid_*` and the other committee prefixes present in the split. They come from the case `domain` of the meeting, not a hard-coded prefix list. |
| report | Summaries grouped by `query_kind` (specific, general, all) and by domain. Collision cases are excluded from summaries but kept in `per_case.jsonl`, with exclusions listed. An extra `summary_ppt.md`: scope A/B × kind × Hit@5 / Recall@5 / pool recall for the production baseline, plus the best scope-B setting. Comparisons stay per scope against that scope's baseline (unchanged contract). |

The metric definitions (`compute_metrics`, ceiling, fusion, diversification
replica) do not change. AC4 (val-30 reproduction) guards this.

## 3. Data flow

```
final/ test.jsonl ──select --split test --all──▶ runs/rq1-test-<date>/selection.json
corpus ─(35 test meetings)─▶ ingest_library ─▶ one test tenant (library.json checkpoint)
                                  │
query ─embed (cached)─▶ fetch_candidate_pools (read-only, scopes A/B) ─▶ 36 settings ─▶ metrics
                                  ▼
          per_case.jsonl, baseline.md, ablation.md/csv, attribution, summary_ppt.md
```

## 4. Where it runs

- Code: the dev working tree (`evals/` changes live there).
- `app/` imported at runtime: the retrieval path is identical between dev and
  main `7418262` (`search.py`, `chunker.py`, `tasks.py`, `services.py`).
  - `embed.py`, `models.py`, `bootstrap.py` and `config.py` differ only by WIP
    multimodal types and config.
  - Implement step 1 re-checks the text-embedding call path. If it differs in
    behaviour, run from a clean detached worktree of `origin/main` with
    `evals/` copied in.
- External-disk I/O errors were seen on 2026-10-08. The run is short (~20–30
  min) and checkpointed: `ingest_library` resumes, and `query_vectors.json` is
  cached. On an I/O error, rerun the same command into the same run directory.
- DB: Neon test branch, via `production_path.prepare_test_environment` (host
  assertion). Retrieval uses `read_only_session_factory`.

## 5. Risks

- **General questions.** Their gold evidence is spread over the whole meeting,
  so Recall@5 is low by construction. They are reported separately and never
  pooled into a headline number without the specific-only number beside it.
- **Test-tenant growth.** A new test tenant with about 35 items and about 2.3k
  segments is created on the test branch. It is not deleted, so the library
  stays reusable; this is recorded in the report.
- **Rollback.** Revert the `evals/` edits; the run directory is disposable.
