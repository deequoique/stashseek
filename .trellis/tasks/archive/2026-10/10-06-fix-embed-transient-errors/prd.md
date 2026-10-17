# 修复 embedding 传输异常：分类并有界重试

Parent: `10-06-meeting-qa-quality`. Independent of RQ1; parallel with the other
fix children. Lightweight task (PRD-only).

## Goal

A transient network failure while embedding should not permanently fail an
ingest. A non-transient provider error must still fail fast, with a classified
`EmbeddingError`.

## Confirmed Facts

- `ZhipuEmbedder._embed_batch` (`app/ingest/embed.py:105-135`) wraps only
  `HTTPError`, `URLError`, `TimeoutError` and `json.JSONDecodeError` into
  `EmbeddingError`.
  - A truncated chunked response raises `http.client.IncompleteRead` during
    `json.load(response)`, and it escapes unwrapped. Observed 2026-10-06 while
    embedding ~900 cues.
  - `ConnectionResetError` and `ssl.SSLError` (both `OSError`) escape the same
    way.
- The worker does not crash on either. `process_dispatch`
  (`app/ingest/tasks.py:936-972`) turns any non-`TransientFetchError` exception
  into a permanent `ingestion_failed` dispatch. Celery autoretries only
  `TransientFetchError` (`app/ingest/tasks.py:477`).
  - Net effect: every transient embedding failure, wrapped or not, permanently
    fails the item. Nothing retries it.
- Query-time embedding (`KnowledgeServices._embed_query`,
  `app/agent/services.py:307-328`) already converts every exception into
  `EmbeddingUnavailable` and goes through the agent's read-recovery path.
- `fix-chunk-merge` (C4) embeds every cue for semantic boundaries, roughly
  doubling ingest embedding calls.

## Requirements

- R1 `_embed_batch` classifies failures:
  - transient: `IncompleteRead` / `http.client.HTTPException`, `OSError`
    (connection reset, SSL, socket timeout), `URLError`, `TimeoutError`,
    HTTP 429 and HTTP 5xx.
  - permanent: other HTTP 4xx, malformed JSON, count/index/dimension mismatch.

  Every failure leaving the embedder is an `EmbeddingError`. The original
  exception is chained, but its message is not copied, which keeps the existing
  redaction behavior.
- R2 Transient failures are retried per batch inside the embedder:
  - at most 3 attempts in total
  - bounded exponential backoff with jitter
  - total added wait ≤ ~10 s per batch

  Permanent failures are not retried. Only the failing batch is re-sent, never
  the whole item.
- R3 Behavior for callers is unchanged apart from fewer failures:
  - same return shape
  - same `EmbeddingError` type and code on exhaustion
  - `process_dispatch` semantics untouched

## Acceptance Criteria

- [ ] Unit tests with a stubbed `urlopen` cover:
  - `IncompleteRead`, then success → vectors returned after a retry
  - repeated `ConnectionResetError` → `EmbeddingError` after 3 attempts
  - HTTP 400 → `EmbeddingError` with no retry
  - HTTP 429 / 503 → retried
  - backoff sleeping is injectable, so tests do not sleep
- [ ] Existing `tests/test_embed.py` passes.

## Out of Scope

- Celery-level retry of embedding failures (would re-embed the whole item).
- Multimodal (`wemm_local`) and media embedding paths.
