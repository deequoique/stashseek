# Video recognition evaluation harness

This directory is a provider-neutral evaluation harness. It does not import
the production ingestion, queue, database, or search runtime and it never
writes production state.

The checked-in `catalog.yaml` is the `benchmark-v1` regression pool. It
contains the 12 user-selected public URLs (six YouTube, six Bilibili), 30
query slots (12 speech, 8 visual, 6 OCR, 4 combined), 11 shortlisted
profiles, three planned runs per profile/sample, the minimum-media boundary,
and the hard quality gates. This remains regression provenance; it is not the
first product-acceptance execution scope.
`catalog_status=dry_run_only` and the query/media annotations are intentionally
pending: no recognition provider may run until the human query truth, time
ranges, frame/audio selections, and subtitle body coverage are reviewed and
the manifest is published as a new frozen revision.

## Small product-acceptance pilot

`pilot_catalog.yaml` is a separate `pilot-v1` planning manifest which references
the regression pool without rewriting it. The initial pilot selects four core
samples (`YT-01`, `YT-03`, `BI-01`, and `BI-06`) and has exactly three pending
query slots per selected sample (12 total). It can grow to at most six samples
and 18 queries when a documented coverage gap requires a risk extension.

Pilot media is modality-driven: only selected samples and modalities required
by their query slots need media selections. It does not mechanically require
two audio windows and two frame bundles for every regression sample. Public
evidence selection, query truth, media selection, and selected-sample content
classification remain closed gates in the checked-in planning manifest; no
default or fallback winner is invented there.

The current planning revision records evidence-bounded candidate query wording,
key terms, conservative distractors, and bounded audio/frame candidates from
the research notes. These are still manual-review inputs: candidate times stay
in annotation notes or media slots with `pending_manual_review`, while query
truth keeps empty expected IDs/ranges and `time_origin: pending`. The
default/fallback research proposal is referenced for review only; it does not
populate profile IDs or authorize a provider call.
Nonempty candidate query, key-term, distractor, audio, frame, and OCR fields are
not frozen truth; they remain reviewable planning inputs until the manifest,
annotations, media, and public-evidence selections are explicitly frozen.

The pilot planner reports one default unit per selected sample/modality after a
future evidence-backed profile selection. A fallback unit is planned only when
an explicit allowed trigger is supplied. The default retry policy is one run
with at most two retries for stable provider-transient or protocol-instability
reasons.

## Safe commands

```bash
.venv/bin/python -m evals.video_recognition --validate-catalog
.venv/bin/python -m evals.video_recognition --validate-pilot
.venv/bin/python -m evals.video_recognition --probe
.venv/bin/python -m evals.video_recognition --dry-run
.venv/bin/python -m evals.video_recognition --pilot-dry-run
.venv/bin/python -m evals.video_recognition --provider-preflight
```

`--validate-pilot` and `--pilot-dry-run` are offline commands. Both validate
the pilot against the regression catalog and report the selected
sample/query/profile state, dynamic potential/default/fallback units, stable
blockers, and `external_calls: 0`. The dry-run additionally loads and validates
the checked-in 12-record probe projection while exercising only local fixture
adapters and cleanup. The validation-only command does not claim to inspect
that snapshot. Neither command opens or reuses the formal `--run` path. A
valid planning manifest normally exits successfully while still reporting a
closed pilot gate; malformed or mismatched manifests exit with code `2`.

`--pilot-catalog` is only accepted together with `--validate-pilot` or
`--pilot-dry-run`; it cannot be silently ignored by a legacy mode. Likewise,
smoke confirmation and probe-output modifiers require their corresponding
dedicated command.

## Opt-in OpenAI connectivity smoke

The isolated smoke path does not load this benchmark's catalog, does not open
the formal provider gate, and never downloads a video. It creates one tiny
deterministic WAV and one tiny deterministic PNG in a marked temporary
directory, then deletes that directory in `finally`. The four requests are
`whisper-1` plus exactly `gpt-5.6-luna`, `gpt-5.6-terra`, and `gpt-5.6-sol`.

External calls are disabled unless both the dedicated command and an explicit
confirmation are present. Load the private `.env` in the calling process; the
smoke code does not read or print `.env` and the API key never appears in the
command line or output:

```bash
.venv/bin/dotenv -f .env run --no-override -- \
  .venv/bin/python -m evals.video_recognition \
  --openai-smoke --confirm-openai-smoke
```

The confirmation can instead be supplied as an explicit environment gate:

```bash
.venv/bin/dotenv -f .env run --no-override -- \
  env VIDEO_RECOGNITION_OPENAI_SMOKE_CONFIRM=1 \
  .venv/bin/python -m evals.video_recognition --openai-smoke
```

The output is a redacted JSON summary containing only model, modality,
success/status, latency, available numeric usage, a deterministic
content-free signal, stable error codes, cleanup status, and—only for an HTTP
failure—a validated integer `http_status`. Transport and response-parse
failures do not include an HTTP status. The smoke may
incur a small provider charge; it is separate from `--run` and does not make
the formal benchmark eligible. Admission/configuration blocks exit with code
`2`; a completed smoke report containing one or more provider failures still
prints the safe report and exits with code `3`.

`--probe` is network-free and emits the checked-in redacted formal projection
from `probe_snapshot.yaml`. `--probe --live` performs a new bounded public yt-dlp
metadata and subtitle-body probe; it does not call ASR, Vision, or OCR
providers. Subtitle bytes are held in memory only, then reduced to cue count,
start/end, and coverage ratio. The live snapshot writer never serializes
subtitle URLs, headers, bodies, or stderr.

`--dry-run` validates every schema and fixture adapter for all 12 regression
samples, verifies redaction, and creates/removes a marked temporary run
directory. It reports `external_calls: 0`, credential presence counts, and the
exact closed execution gate. Provider adapters are deliberately disabled even
if an environment variable is present; this avoids an accidental paid call.

`--pilot-dry-run` performs the same kind of local fixture and cleanup checks
only for pilot-selected sample/modality units, then reports dynamic pilot
planning counts. Neither dry-run command downloads media or calls ASR, Vision,
OCR, or any other provider.

## Protocols

- `media-sample-bundle.v1`: only bounded audio ranges and representative frame
  timestamps; no complete media, URL, cookie, token, or filesystem path.
- `asr.v1`: finite ordered segments/words and stable timestamps.
- `vision.v1`: short search-oriented observations and evidence frame times.
- `ocr.v1`: text, four-point normalized polygons, confidence, and frame time.
- `discovery-segment.v1`: provider-independent search projection.
- `recognition-result.v1` and `recognition-error.v1`: explicit terminal states
  (`completed`, `provider_incomplete`, `harness_invalid`) and safe error codes.

Protocol examples live under `fixtures/`; they contain no SDK objects or raw
provider responses.

## Rights and media boundary

Public viewing and user selection are not treated as open-reuse permission.
Rights uncertainty for every sample remains visible in the catalog. Any later
execution must use only the smallest frozen temporary windows/frame bundles,
must not process private/login/DRM media, and must delete temporary inputs and
provider responses after the run. Complete media and subtitles are never
stored as fixtures.
