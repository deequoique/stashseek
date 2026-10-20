# Ingestion Chunking and Embedding

## Scenario: semantic-first transcript chunking with lower/upper bounds

### 1. Scope / Trigger

Apply this contract whenever you change `app/ingest/chunker.py`, the
`chunk()` call sites in `app/ingest/tasks.py`, or any consumer that assumes a
segment size. The consumers are:

- retrieval `search_segments` / `get_neighbors`;
- the answer composer excerpt cap;
- evaluation harnesses that map segments back to cues.

Segment size is a cross-layer contract. Ingest decides it, retrieval ranks it,
and the answer stage reads it.

Why this contract exists:

- Before 2026-10-06, every gap, punctuation and semantic boundary was a
  mandatory split, with only a ≤120 s ceiling. Real meeting caption tracks came
  out at a median of 12 words per segment, and 32% of segments had ≤5 words.
  The documented 60 s / 170-word target applied only to the rarely reached hard
  cut.
- Details: `.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/findings-20261006.md`.

### 2. Signatures

```python
# app/ingest/chunker.py
LOWER_UNITS = {"en": 80, "zh": 130}     # words / CJK chars (via _units)
UPPER_UNITS = {"en": 200, "zh": 330}
MAX_CHUNK_SECONDS = 120
OVERLAP_RATIO = 0.15

def chunk(cues: list[Cue], *, lang: str, chapters: list[dict] | None = None,
          semantic_embedder: Callable[[list[str]], list[list[float]]] | None = None,
          ) -> list[Chunk]
# Chunk(start_sec, end_sec, text, boundary_kind)
# boundary_kind ∈ {"chapter", "gap", "punct", "semantic", "hard_cut"}

# app/agent/runtime_state.py
COMPOSER_EVIDENCE_EXCERPT_CHARS = 1200  # answer composer, stream plan, and section stream
```

### 3. Contracts

- **Chapters** keep priority. A chapter ≤180 s becomes one `chapter` chunk; a
  longer chapter recurses into the window walk.
- **Window walk.** From `start`, the candidate cut positions are every `j` where
  `[start, j)` has ≥ `LOWER` units, ≤ `UPPER` units, and lasts ≤120 s.
  - **With per-cue embeddings**, the cut goes at the deepest TextTiling-style
    similarity dip: `(left peak − sim) + (right peak − sim)`. A sentence end or
    gap ≥2 s whose depth is ≥50% of the window maximum wins over a deeper
    mid-sentence dip. Label: `semantic`, or `gap` / `punct` when a preferred
    boundary wins.
  - **Without embeddings**, the cut goes at the largest gap ≥2 s, else at the
    sentence end nearest the window middle, else at the end of the window
    (`hard_cut`).
  - **No candidate** (the lower bound cannot be reached within 120 s): extend
    to the last cue that keeps ≤120 s (`hard_cut`). A transcript shorter than
    the lower bound is exactly one chunk.
- **Overlap.** The next chunk starts `max(1, round(0.15 × cues_in_previous))`
  whole cues before the previous end, and always extends coverage. If that
  retreat would lead to a cut at or before the previous end (a very long gap),
  the overlap is dropped for that boundary.
- **Tail merge.** A final piece below `LOWER` merges into the previous chunk
  when the result stays ≤ `UPPER` and ≤120 s.
- **Mapping.** `Chunk.text` is the joined stripped cue texts, and
  `start_sec` / `end_sec` equal the first and last cue times. Callers map
  segments back to cues by time range.
- **Semantic embedder usage** (`process_item`):
  - Every cue is embedded once (one call), then every final chunk once (one
    call).
  - Browser captures above `MAX_SEMANTIC_BOUNDARY_CUES = 512` cues get no
    per-cue embedding.
  - Budget guard: if `cue_chars + 1.2·cue_chars > ingest_max_embedding_chars_per_item`
    (remaining), skip semantic boundaries and chunk on gap/punct instead of
    raising `IngestLimitExceeded`.
  - The preflight `chunk()` call (segment-count limit) never passes an
    embedder.
- **Excerpt cap.** All answer-stage evidence renderings use
  `COMPOSER_EVIDENCE_EXCERPT_CHARS` (1,200). No literal caps.
- Existing segments are not re-chunked. Old and new segment shapes coexist.

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Punctuated track, sentence-per-cue | Chunks merge to [80, 200] EN words; no single-sentence chunks except a short transcript |
| Span with no boundary before 120 s | `hard_cut` at the last cue ≤120 s |
| Single cue > `UPPER` units | That cue alone is one chunk (the upper bound may be exceeded only here) |
| Gap > 120 s right after a cut | No duplicate or zero-progress chunk; coverage strictly advances |
| Empty cue list | `[]` |
| Embedding budget too small for the semantic pass | Gap/punct chunking; ingest still completes |
| Browser capture > 512 cues | No per-cue embedding call; gap/punct chunking |

### 5. Good/Base/Bad Cases

- Good: a 90-minute meeting caption track gives ~60–140 chunks of 80–200 words,
  each ≤120 s, cut at topic dips and adjacent chunks sharing 1–3 cues.
- Base: an unpunctuated auto-caption track without embeddings gives chunks near
  the upper bound, labelled `hard_cut`.
- Bad: splitting at every punctuation boundary. This produces 5-word segments
  that are neither retrievable nor citable.

### 6. Tests Required

`tests/test_chunker.py`:

- `test_dense_punctuation_track_merges_to_lower_and_upper_bounds`
- `test_semantic_deepest_dip_is_chosen`
- `test_sentence_end_preferred_within_half_max_depth`
- `test_gap_preferred_without_embeddings`
- `test_long_span_with_no_boundary_uses_hard_cut`
- `test_overlap_shares_whole_cues_and_always_advances`
- `test_overlap_retreat_never_produces_a_zero_progress_duplicate_chunk`
- `test_tail_merge_within_limits`
- `test_tail_kept_when_merge_would_exceed_bounds`
- `test_cjk_unit_bounds`
- `test_chapters_are_first_priority`
- `test_short_whole_transcript_stays_one_chunk_below_lower_bound`

`tests/test_tasks.py`:

- the semantic call shape (≤512 cues: per-cue call then chunk call; >512
  capture: no per-cue call)
- `test_semantic_budget_guard_skips_per_cue_embedding_when_insufficient`

### 7. Wrong vs Correct

#### Wrong

```python
# every boundary is a mandatory split; only the 120 s ceiling is checked
pieces = _split_at(cues, boundaries)
return pieces if all(p.end_sec - p.start_sec <= 120 for p in pieces) else None
```

#### Correct

```python
# boundaries are candidates; never cut below LOWER; pick the deepest dip in
# [LOWER, min(UPPER, 120 s)]; overlap whole cues; always advance
candidates = _window_candidates(cues, lang, lang_key, start)
winner, label = _choose_cut(cues, lang, lang_key, start, candidates, embeddings)
```

---

## Scenario: transient embedding failures are retried per batch

### 1. Scope / Trigger

Apply this contract when you change `ZhipuEmbedder` (`app/ingest/embed.py`) or
how ingestion handles embedding errors. The worker
(`process_dispatch`) turns every non-`TransientFetchError` exception into a
permanent `ingestion_failed`. Without embedder-level retry, a single dropped
connection permanently fails an item, and semantic-first chunking doubles the
number of ingest embedding calls.

### 2. Signatures

```python
# app/ingest/embed.py
_MAX_EMBED_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_BACKOFF_MULTIPLIER = 2.0
_RETRY_MAX_DELAY_SECONDS = 4.0

class ZhipuEmbedder:
    def __init__(self, api_key, *, model=..., endpoint=..., dimensions=1536,
                 batch_size=MAX_BATCH_SIZE, ssl_context=None,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None) -> None
```

### 3. Contracts

- Only the failing batch's HTTP request is retried, never the whole item.
- At most 3 attempts per batch. The delay before retry `i` is
  `rng.uniform(0, min(4.0, 1.0·2^i))`, so the worst-case added wait is 3 s per
  batch.
- Transient: `HTTPError` 429 or 5xx, `http.client.HTTPException` (including
  `IncompleteRead`), `OSError` (connection reset, SSL, socket timeout),
  `URLError`, `TimeoutError`.
- Permanent (no retry): other `HTTPError` 4xx, `json.JSONDecodeError`, and
  response count/index/dimension mismatches.
- `HTTPError` is classified **before** the broader `URLError` / `OSError`
  checks, because it subclasses both.
- Every failure leaves the embedder as
  `EmbeddingError("embedding request failed")` with `from exc` chaining. The
  provider message is never copied (see `provider-tls-diagnostics.md`).
- Worker semantics are unchanged: an exhausted retry still yields
  `ingestion_failed`, and there is no Celery-level re-embedding.
- `urlopen` responses are always closed by a context manager on every attempt.

### 4. Validation & Error Matrix

| Provider behavior | Result |
|---|---|
| `IncompleteRead` once, then 200 | Vectors returned; 2 attempts |
| 429 / 503, then 200 | Vectors returned |
| `ConnectionResetError` ×3 | `EmbeddingError`; exactly 3 attempts |
| HTTP 400 | `EmbeddingError`; 1 attempt, no sleep |
| Malformed JSON body | `EmbeddingError`; 1 attempt |

### 5. Good/Base/Bad Cases

- Good: a 1,400-cue ingest survives one truncated chunked response mid-way.
- Base: the provider is down for >3 s on a batch, so the item fails as
  `ingestion_failed`, as before.
- Bad: catching only `HTTPError` / `URLError`. `IncompleteRead` escapes, and
  nothing is retried.

### 6. Tests Required

`tests/test_embed.py`:

- `test_embed_retries_incomplete_read_then_succeeds`
- `test_embed_retries_http_429_and_503_then_succeeds`
- `test_embed_raises_after_three_attempts_on_repeated_connection_reset`
- `test_embed_http_400_fails_without_retry`
- `test_embed_malformed_json_fails_without_retry`
- `test_embed_retry_backoff_never_sleeps_for_real` (the injected `sleep`;
  real `time.sleep` must not be called)

### 7. Wrong vs Correct

#### Wrong

```python
except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
    raise EmbeddingError("embedding request failed") from exc   # IncompleteRead escapes; nothing retried
```

#### Correct

```python
for attempt in range(_MAX_EMBED_ATTEMPTS):
    try:
        with urlopen(request, timeout=60, context=self._ssl_context) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, http.client.HTTPException, OSError, json.JSONDecodeError) as exc:
        if attempt == _MAX_EMBED_ATTEMPTS - 1 or not self._is_transient_error(exc):
            raise EmbeddingError("embedding request failed") from exc
        self._sleep(self._retry_delay_seconds(attempt))
```

## Scenario: title row and Chinese fts

### 1. Scope / Trigger

Apply this contract whenever you touch the segment insert in `process_item`,
add a writer of `segment` rows, enumerate an item's segments by `seq`, or
change how Chinese text is indexed. Origin: task `10-20-cn-recall-fusion`
(2026-10-20). Two gaps motivated it: titles and descriptions were not
retrievable at all, and Chinese segments had `fts = NULL` with a pg_trgm
lexical path that only matched whole-query substrings.

### 2. Signatures

```python
# app/ingest/title_row.py
TITLE_ROW_SEQ = -1
TITLE_ROW_KIND = "title"
TITLE_ROW_DESCRIPTION_CHARS = 400
def title_row_text(item) -> str                       # title (or platform_id) + "\n" + description[:400]
def title_row_fts(text: str, lang: str | None)        # zh* -> zh_tsvector; else to_tsvector('english'), || zh_tsvector when the text has CJK
def build_title_row(item, *, text: str, embedding: list[float]) -> Segment
#   Segment(item_id, seq=-1, start_sec=0, end_sec=0, text, embedding, fts, boundary_kind="title")

# app/retrieval/zh_tokens.py
QUERY_TOKEN_LIMIT = 64
def cjk_tokens(text: str, *, max_tokens: int | None = None) -> list[str]
def zh_tsvector(text: str)      # func.to_tsvector("simple", " ".join(cjk_tokens(text)))   -- no cap
def zh_tsquery(query: str)      # func.websearch_to_tsquery("simple", " OR ".join(tokens)) or None

# app/ingest/reindex.py
@dataclass(frozen=True)
class ReindexResult:
    zh_segments_updated: int; title_rows_inserted: int; title_rows_updated: int
    title_rows_skipped: int; failed_item_ids: tuple[int, ...]; dry_run: bool
def reindex(*, user_id: int | None = None, dry_run: bool = False, zh_fts: bool = True,
            title_rows: bool = True, embedder=None, session_factory=None) -> ReindexResult

# CLI
python -m app.cli reindex [--user-id N] [--dry-run] [--zh-fts] [--title-rows]   # exit 2 on any failed item
```

### 3. Contracts

- **One title row per ready item.** `process_item` embeds
  `[title_text, *chunk_texts]` in one batch (title text counts against the
  same embedding budget), deletes the old segments, inserts the title row
  first and then the body chunks. The body-segment limit
  (`ingest_max_segments_per_item`) counts body chunks only. The row satisfies
  `loc_ck` with `start_sec = 0`; that zero is a placeholder, never a time:
  retrieval projects the row to `Citation(kind="title", start_sec=None)`.
- **Body enumeration excludes title rows.** Anything that walks an item's
  segments by `seq` (neighbors, open-at, evaluation cue mapping, transcript
  views) must filter `boundary_kind != "title"` or `seq >= 0`.
- **Chinese fts.** For `lang` starting with `zh`, body segments and the title
  row store `to_tsvector('simple', <tokens>)` where tokens are CJK character
  bigrams (a one-character run stays a unigram), ASCII words lower-cased,
  bigrams containing a sentence particle (的 了 吗 呢 吧 啊 呀) dropped,
  deduplicated in first-seen order. Documents are never capped; queries are
  capped at 64 tokens. Other languages keep `to_tsvector('english', text)`
  for body segments; a non-Chinese item's title row additionally
  concatenates the bigram vector (`english || simple`) when the title text
  contains CJK, so a Chinese title over English captions stays lexically
  searchable in Chinese. No new PostgreSQL extension is required (Neon
  offers no zhparser/pg_jieba).
- **Backfill is an operator command, not a migration.** `app.cli reindex`
  fills `fts` for Chinese segments where it is NULL (SQL only, no embedding
  call) and inserts or refreshes title rows (one embedding call per item,
  only when missing or when the text changed). It is idempotent, scoped to
  ready/undeleted/unarchived/unpurged items, optionally one tenant, commits
  per item, records per-item failures and continues. Output is counts and
  item IDs only. Dry run never builds the embedder and never writes.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| item has no title | title row text is the `platform_id` |
| description longer than 400 chars | truncated to 400 chars in the title row |
| embedding batch returns `len(chunks)` vectors instead of `len(chunks) + 1` | `ValueError` (count mismatch), item not marked ready |
| reindex embedding or DB failure for one item | that item rolled back and listed in `failed_item_ids`; others continue; CLI exits 2 |
| reindex `--dry-run` | counts only; no embedder construction, no writes |
| Chinese query tokenizes to nothing | lexical path returns `[]`; vector path unaffected |
| single CJK character query | matches only one-character runs (bigram index); vector path carries it |

### 5. Good/Base/Bad Cases

- Good: a Bilibili item with English captions but a Chinese title is found by
  a Chinese question through its title row.
- Base: an English item gets a title row with `english` fts and its body
  segments exactly as before, one extra embedding per item.
- Bad: enumerate `Segment` by `seq` without excluding `title`, cite the title
  row with a `t=0` timestamp, cap `zh_tsvector` at 64 tokens (only the start
  of a segment would be indexed), or run the backfill inside an Alembic
  migration (no embedding provider there).

### 6. Tests Required

- `tests/test_tasks.py`: exactly one title row (`seq -1`, kind `title`,
  `start_sec 0`); batch starts with the title text and has `chunks + 1`
  entries; zh items use `simple` for body and title row, en items `english`;
  the segment limit ignores the title row; title falls back to `platform_id`.
- `tests/test_asr_ingest_flow.py` and `tests/test_bilibili_multipart_extract.py`:
  body assertions skip title rows.
- `tests/test_reindex_cli.py`: dry run writes nothing; only missing or stale
  title rows are embedded; `--user-id` filter; zh fts backfill makes no
  embedding call; one failing item does not stop the others and exits 2.
- `tests/test_zh_tokens.py`: bigrams, unigram run, ASCII lower-casing,
  particle bigrams skipped, dedup, cap, empty/punctuation-only input.

### 7. Wrong vs Correct

#### Wrong

```python
fts = func.to_tsvector("english", part.text) if not lang.startswith("zh") else None
neighbors = select(Segment).where(Segment.item_id == item.id, Segment.seq.between(lo, hi))
```

#### Correct

```python
fts = zh_tsvector(part.text) if lang.startswith("zh") else func.to_tsvector("english", part.text)
db.add(build_title_row(item, text=title_text, embedding=vectors[0]))
neighbors = select(Segment).where(
    Segment.item_id == item.id, Segment.boundary_kind != "title", Segment.seq.between(lo, hi)
)
```
