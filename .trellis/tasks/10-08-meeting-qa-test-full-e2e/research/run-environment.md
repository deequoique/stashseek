# Run environment: dev working tree vs origin/main (2026-10-08)

Question (implement.md step 1, design.md section 4): can the RQ1 test-split run import `app/` from the dev
working tree, or must it use a clean detached worktree of `origin/main`?

Compared: dev working tree (uncommitted WIP on `dev`, HEAD `8b40673`) against `origin/main` = `7418262`
using `git diff origin/main -- <file>`.

## Retrieval and ingest path: byte-identical

`git diff origin/main --stat` is empty for:

- `app/ingest/tasks.py` (`create_item`, `process_item`, `build_worker_embedder` at tasks.py:927-941)
- `app/ingest/chunker.py`
- `app/retrieval/search.py` (`vector_search`, `bm25_search`, `_hits`)
- `app/agent/services.py` (`KnowledgeServices.search_segments`, `_diversify_hits`)

## Text-embedding path: behaviour identical

| File | Diff vs origin/main | Effect on text embedding |
|---|---|---|
| `app/ingest/embed.py` | +57/-3 | Additions only: `EmbeddingInputError`, `EmbeddingSpaceMetadata`, `MediaObjectReference`, `MultimodalInput`, `MultimodalEmbeddingProvider` Protocol, a lazy module `__getattr__` for `WemmLocalEmbedder`, a docstring edit, and `_fetch_batch_response(...) -> Any` (return annotation only). `ZhipuEmbedder` is unchanged: request payload `{model, input, dimensions}` (embed.py:123-129), `MAX_BATCH_SIZE = 64` and per-`batch_size` slicing (embed.py:114-120), 3 attempts with 1.0s base / x2 / 4.0s cap backoff (`_MAX_EMBED_ATTEMPTS`, `_RETRY_*`, embed.py:20-23 and :172-210; main has the same constants at :19-22 and :130-168). The only shifted lines are the inserted types above the class. |
| `app/bootstrap.py` | +46/-1 | `build_embedding_provider` (bootstrap.py:28-45) is textually identical to main (main :23-40): same `ZhipuEmbedder(api_key, model=embedding_model, endpoint=embedding_endpoint, dimensions=embedding_dimensions, batch_size=embedding_batch_size, ssl_context=...)`. The diff adds `build_multimodal_embedding_provider` (returns `None` unless `multimodal_retrieval_enabled and wemm_enabled`) and wires it into `build_knowledge_agent` only. The RQ1 harness never calls `build_knowledge_agent`. |
| `app/config.py` | +97/-1 | Adds WEMM settings (`wemm_*`, `multimodal_retrieval_enabled`, both default off, resolved lazily by `_WemmEnv`) and `_validate_wemm`, which runs only when both flags are enabled. `embedding_model`, `embedding_endpoint`, `embedding_dimensions`, `embedding_batch_size`, `zhipu_api_key` are untouched. The one other change is the `WEB_STATIC_DIR` default (`web/dist` to `extension/dist`), which is irrelevant to retrieval. |

## Schema: additive only

`app/models.py` diff (+132/-0, no removed lines): adds `UniqueConstraint("id", "user_id", name="uq_content_item_id_user_id")` on
`ContentItem`, and new `MediaAsset` / `MediaEmbedding` tables. `ContentItem` and `Segment` columns used by
`create_item` / `process_item` / `vector_search` / `bm25_search` are not changed. The harness imports `app.models`
only for `AppUser`, `ContentItem`, `Segment`; it never touches the media tables.

## Decision

Text-embedding behaviour (request payload, model, dimensions, batching, retry) is identical, and the full
retrieval, chunking and ingest path is byte-identical. The RQ1 test-split run can import `app/` from the dev
working tree. No detached worktree of `origin/main` is needed.

Caveat: `evals/meeting_gold/production_path.py` records `git_sha` from the dev HEAD (`8b40673`), not `7418262`.
The run report should state that `app/` retrieval/ingest files equal `origin/main` `7418262`, as verified here.
The external-disk I/O retry plan in design.md section 4 still applies (the run is checkpointed and resumable).

## AC4 — val-30 reproduction (main session, 2026-10-08)

- Command: `.venv/bin/python -m evals.meeting_gold rq1 --split val --output data/meeting_gold/runs/rq1-val-repro-20261008`.
  The run directory was seeded with `library.json` and `query_vectors.json`
  from `rq1-20261006`, so it used the same test-branch items and the same
  query vectors and made no new embedding calls.
- Result:
  - `status=ok`, 30 cases, 28 eligible, 0 excluded;
  - `r2_failures=0`, `scope_a_invariance_failures=0`.
- `baseline.md` (lines 1–30) and `ablation.csv` are **byte-identical** to
  `rq1-20261006`. Scope A Recall@5 is 0.381 and scope B is 0.090 (n=28).
  AC4 passes.
- The new outputs match the 10-08 manual analysis:
  - scope B, production setting: top-1 is the right meeting 13/28, the right
    meeting is in the top 5 20/28, and 12 of the 15 wrong top-1s come from the
    same source.
  - new metric, candidate-pool recall (production AND pool): A 0.861, B 0.458.
- Rerun with the final post-check code (`rq1-val-repro2-20261008`, seeded the same way):
  - `status=ok`;
  - `baseline.md` identical;
  - all 28 `ablation.csv` rows identical on every shared column. The only
    additions are the new `hit@5_k`, `hit@5_wilson_low` and
    `hit@5_wilson_high` columns from the check.
  - AC4 holds on the final code.

## Test-split run, spot-check and integrity (main session, 2026-10-08)

- `rq1 --split test --selection data/meeting_gold/runs/rq1-test-20261008/selection.json --output data/meeting_gold/runs/rq1-test-20261008`
  - `status=ok`, 281 cases, 270 counted, 1 held-out excluded (also CES-ineligible);
  - `r2_failures=0`, `scope_a_invariance_failures=0`;
  - every scope-B dense pool returned 50/50 candidates.
- Spot-check (`scratchpad/spot_check.py`, seed 20261008): 4 specific cases and 1 general case, scopes A and B.
  - I recomputed Recall@5 and the localization fields from `library.json` and
    the per-case segment ids.
  - I recomputed the AND candidate-pool recall by re-fetching the pools
    read-only on the test branch.
  - All 10 scope-records match.
- Integrity after the run: 35/35 meetings pass `_meeting_still_valid`; the tenant holds 35 items and 2,361 segments.
