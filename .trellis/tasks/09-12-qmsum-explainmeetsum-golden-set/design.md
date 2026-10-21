# Meeting Golden Set Design

## Boundaries

Add an evaluator-owned package under `evals/meeting_gold/`. Reuse the repository's
strict Pydantic validation, pathlib, JSON, hashing, and pytest conventions. Do
not widen `HumanEvalDataset` or call `score_gold_sample` with fabricated video
metadata. Existing `GoldSample.is_gold_complete` requires timestamps, and its
dataset caps cases at 30; neither constraint models this source collection.

Use the published pre-split ExplainMeetSum data, not model outputs, oracle
folders, or a fresh sentence tokenizer. Do not install the upstream training
stack or run upstream code. Keep raw and generated full corpora in an ignored
local data directory; commit converter code, a versioned source lock, notices,
documentation, and small attributed test fixtures only.

## Sources And Reproducibility

Proposed source lock, inspected on 2026-09-12:

| Source | Commit | Canonical input |
| --- | --- | --- |
| Yale-LILY/QMSum | `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e` | `data/ALL/{train,val,test}/*.json` |
| hkim-etri/ExplainMeetSum | `d8459311a6578cd438a785bf19dd2fa757ade1c5` | `data/ExplainMeetSum/{train,val,test}/*.json` |

Acquisition is explicit, separate from offline build/validate, and restricted
to these repositories/revisions. Support local checkouts; validate HEAD and
canonical input file hashes, including untracked additions/deletions in input
paths. No moving `main` or automatic source refresh during build. Capture file
SHA-256, license/README source hashes, converter version, and schema version in
the lock/manifest. Include both upstream MIT notices and paper attribution;
do not assume those notices license separately downloaded audio/video.

Canonical serialization uses UTF-8 without BOM, `ensure_ascii=False`, sorted keys,
compact separators `(',', ':')`, `allow_nan=False`, and exactly one LF after each
JSON value/JSONL record (empty JSONL has zero bytes). Preserve Unicode codepoints
without normalization, reject duplicate JSON keys, and fix record ordering. Include
no machine paths or wall-clock timestamps in hashed content. An optional receipt may contain operational
details outside the deterministic payload. Build in a temporary output directory
and publish only after validation; never overwrite an existing different revision.
Define `dataset_hash` as SHA-256 of canonical JSON describing schema/converter
versions, source-lock hash, validation/evidence/leakage policy versions, counts,
limitations, freeze-baseline hash and a sorted filename to
SHA-256 map for `corpus.jsonl`, the three case files, `audit.json`, `NOTICE.md`
and both source license files. This descriptor excludes manifest, selection,
receipts and the dataset hash itself. Manifest embeds the descriptor and hash;
readers recompute them and validate the exact payload file inventory. Selection
references the base hash and never contributes to it, avoiding circular hashes.
All normative manifest fields belong to this hashed descriptor; no unhashed
duplicate field may override them. File entries are normalized relative POSIX
paths from a fixed allowlist: reject absolute paths, `..`, symlinks (including
ancestor symlinks below the supplied root), non-regular files, and resolved paths
outside the root before reading. Apply equivalent input containment checks.

## Data Contract

- `manifest.json`: dataset/schema/converter versions, both commits, source and
  output hashes, counts, split/selection/evidence policy, explicit limitations.
- `corpus.jsonl`: stable meeting ID, split, domain, original ordered QMSum turns
  and speakers, ExplainMeetSum sentence records and their original indices,
  optional dialogue-act IDs. No query, reference answer, or evidence labels.
- `{train,val,test}.jsonl`: stable case ID, meeting ID, original query-list kind
  and index, query, QMSum reference answer, provenance, relevant turn spans,
  ExplainMeetSum answer annotations, evidence references/types, eligibility.
- `audit.json`: per-split/domain counts plus stable IDs/reason codes for missing,
  ambiguous, invalid and partial annotations, and duplicate/leakage findings.
- `selection.json`: selected stable IDs, split, selection version/seed, and
  source dataset hash for the optional smoke view. Default val-only, up to 30
  cases, balanced across available domains/query types; report shortfalls.
- `NOTICE.md` and upstream license files: source attribution and limitations.

Case identity: `qmsum:<meeting>:<general|specific>:<zero-based-query-index>`.
Sentence identity: `qmsum:<meeting>:t<turn_index>:s<sent_index>` within the frozen
revision. Never derive either from database IDs or global question text.
Preserve `answer_sentence_index` as the supplied list: it may represent merged
sentences, and must not be flattened into guessed independently supported facts.
Unknown schema majors, duplicate keys/IDs, invalid numeric indices and malformed
records fail explicitly. Do not silently truncate long meetings or reference answers.

## Alignment And Eligibility

1. Inventory canonical files and check meeting split membership before joining.
   QMSum is authoritative for queries, answers, raw turns, and split membership.
   Resolve missing domain labels from pinned QMSum per-domain file membership,
   requiring exactly one Academic/Product/Committee path with bytes matching the
   ALL file. Domain copies are metadata evidence only, never additional cases.
   Freeze the membership/checksum mapping in the source lock; map Academic to
   Academic(icsi), Product to Product(ami), and Committee to Committee. Cross-check
   EMS labels when available. This also assigns covid_1 without filename guessing.
2. Join by split + meeting filename + query-list kind with one-to-one consumption.
   Index both lists by the exact `(query, answer)` tuple before assigning any
   matches. Accept a tuple only when it occurs exactly once on each side;
   ordinal agreement is not a tie-breaker for duplicated text. Retain both source
   ordinals, consume each at most once and audit all unused records on both sides.
   Never use fuzzy/LLM joins or query-only fallbacks.
3. Validate original turn count/order/speaker/content across the two sources.
   Retain both raw versions and quarantine disagreements; any later normalization
   must be explicitly versioned, proven on the full corpus and reviewed.
4. Read only `explainable_qmsum`, not `explainable_ami`/`explainable_icsi` summaries.
   Preserve `explainable_answer` groups and CES/PES labels independently; unknown
   labels require review rather than silently becoming positive evidence.
5. Resolve each annotation's turn/sentence indices against published
   `sentence_level_content`; validate supplied indices, speaker and exact
   `content` against `dialogue_sentence`. Validate answer grouping and source
   summary reconciliation using the upstream stored segmentation; do not
   re-tokenize with a new NLTK version or require naive whitespace concatenation.
   Unresolved segmentation/text differences are quarantined with reasons.
6. Store `eligibility` as a map with required keys `ces` and `ces_pes`. Classify
   each case exactly once PER POLICY as `answer_only`, `citation_scorable`,
   `partial_annotation`, or `quarantined`. Full scorable status requires every
   evidence group to resolve and be non-empty under the selected policy; CES-only
   and CES+PES may differ. Partial groups remain inspectable but are excluded
   from full scoring. Missing evidence is not a negative case.

### Answer Mapping Validation

Store each group's answer text, original index array, and evidence intact. The
index array must consist of strictly increasing non-negative integers (not bools),
be non-empty, and contain consecutive indices. In group order, arrays must not
overlap and their flattened union must be `0..N-1`. This structural check alone
does not prove that the trailing reference answer has been annotated.

Additionally reconcile the complete group text against the matched QMSum answer:
the initial strict policy consumes each non-empty group text literally, in order,
from the answer, permitting only whitespace in uncovered gaps and at boundaries.
Non-whitespace prefixes, suffixes, omitted interior text, reorderings and altered
words fail this check. Repeated text must be consumed by offsets, not set membership
or independent substring tests. No punctuation or case folding is implicit.
Group text is an intact merged annotation, not independently evidenced atomic claims.

Inspect upstream `data/SummaryEvidence` during audit if extra segmentation truth
is needed; any file used in validation becomes a pinned/hashed input. An alternative
reconciliation policy requires a source-backed audit and supervisor review before
use. Store its exact version and test concrete accepted and rejected examples.
Until reviewed, mismatched text/indices are quarantined, not declared complete.

### Eligibility And Freeze Gate

Per-policy state priority is `quarantined` > `answer_only` > `partial_annotation` >
`citation_scorable`. Hard failures include ambiguous joins, mismatched source
text/indices, invalid evidence pointers/types and contradictory labels within one
group. A hard failure anywhere quarantines the whole case; do not score the good
fragments. Missing counterpart queries are answer-only with an explicit reason;
an empty/absent annotation collection in a matched query is partial, not answer-only.
After structural/text validation, a case is partial under a policy if any group
has no evidence after filtering; otherwise all groups are scorable. Keep original
group count and filtered-empty count, and expose eligibility separately for `ces`
and `ces_pes`. Do not drop filtered-empty groups to raise coverage.

Source-format errors preventing reliable record accounting, wrong source bytes,
duplicate meeting identity across splits and repeated consumption abort the build.
A candidate audit may contain quarantined/partial cases; an accepted freeze may
not be inferred simply because files exist. First full audit must obtain supervisor
approval for an exact baseline by split/domain/query type/policy, including stable
IDs, reasons, known omissions and all non-scorable cases. Store this baseline with
the source lock. Future freezes reject any changed counts or exception identities
unless the baseline is explicitly re-reviewed as a new revision. Every represented
split/domain must have at least one fully CES+PES-scorable case; zero cases blocks
acceptance even when counts otherwise reconcile. The known `covid_1.json` omission
is expected, but its affected query count must be measured.

### Split Leakage Policy

Preserve official split assignments in the base artifacts. Duplicate meeting IDs
across splits are structural errors. Different meeting IDs with an identical hash
of the canonical ordered raw `(speaker, content)` turn sequence are retained and
reported with both IDs and splits. Such duplicates touching test block held-out
certification (`held_out_eligible=false`), but not a diagnostic base build.
An explicit selection may exclude affected cases with recorded reasons; it may
claim held-out eligibility only if none of its meetings duplicate a train/val
meeting in the base corpus. Do not silently delete or move official records.
Also audit exact non-empty answer hashes and exact `(query_kind, query, answer)`
tuple hashes across splits. Test overlaps with train/val block held-out eligibility
for affected cases, with explicit collision types; repeated query text alone is
not a leakage error. This conservative check may flag generic repeated answers;
do not silently exempt them. Train/val selections use `not_applicable` for held-out
eligibility. This exact check does not certify absence of near-duplicates or model pretraining
contamination, and reports must disclose that limitation.

## Offline Diagnostics

Accept externally produced ranked retrieval sentence IDs and cited sentence IDs,
bound to dataset hash and case ID. Resolve IDs through the corpus; reject unknown
IDs and wrong revisions. Valid IDs from other meetings count as misses. Deduplicate
repeated IDs without changing rank order. Missing prediction records are reported
as missing, not silently removed from a purported complete evaluation.

Every score run requires a selection, including full-split runs (represented by a
selection of all split IDs). The selection records base dataset hash, one split,
ordered unique case IDs, seed and selection-policy version; its own hash excludes
only its hash field. Predictions carry both hashes and a record list, not a JSON
map that can silently overwrite duplicate case IDs. Reject stale hashes, duplicate
or unexpected case IDs and invalid sentence IDs. A present record must explicitly
provide both retrieval and citation arrays; empty arrays mean a completed attempt
with no hits. A missing record is not equivalent to an empty array.

Default complete mode returns `status=incomplete`, a nonzero CLI exit code and
null aggregate metrics if any selected record is missing. Per-record diagnostics
may remain visible. Explicit partial mode returns `status=partial` and labels all
aggregates as subset-only, with selected, present, missing, eligible and omitted
counts per split/domain/policy. Ineligible records still require an explicit
prediction record in complete mode; their metrics stay unavailable and do not enter
quality denominators. Hash/schema errors are errors in either mode. An empty
selection is rejected. Neither mode produces a semantic quality verdict.
Reports bind scorer-contract version, evidence policy, mode, k-values and canonical
prediction hash in an explicit run-configuration descriptor. Reject unknown versions.

Report separate policies for CES-only and CES+PES; preserve both source labels.
For a policy with eligible non-empty Gold groups `G_j` and cited IDs `C`:

- `gold_evidence_recall = |C intersect union(G_j)| / |union(G_j)|`.
- `gold_citation_precision = |C intersect union(G_j)| / |C|` (0 for empty C
  on an eligible case). This is overlap with annotations, not semantic precision.
- `reference_group_any_hit = mean(1[C intersects G_j])` is a lenient proxy;
  one hit does not mean all information in a summary sentence is supported.
- `reference_group_all_hit = mean(1[G_j subset C])` is strict annotation
  coverage, not a claim that all annotated sentences are semantically necessary.
- Retrieval recall@k uses unique relevant sentence IDs / all Gold sentence IDs;
  hit@k and MRR may be separate fields. Do not reuse video item hit-rate labels.

Expose per-case numerator/denominator, policy-filtered group counts, query-macro
and meeting-macro aggregates per split/domain. Zero eligible groups => unavailable,
not perfect or zero. No overall metric mixes train, val and test by default.
Define `S` as selected IDs, `P` as present selected predictions and `E` as selected
cases scorable under the requested policy. Partial metrics use `P intersect E`.
Report `|S|`, `|P|`, `|S-P|` (missing), `|S-E|` (ineligible), `|E-P|`
(eligible missing), and `|P intersect E|` (scored) separately. Query macro is an
unweighted mean of scored case metrics. Meeting macro first averages scored cases
within each meeting then averages those meeting means equally; meetings with no
scored case contribute no numeric value and their count is explicit. Apply these
definitions independently per split/domain; missing or ineligible cases never
become implicit zeroes or disappear from coverage counts.

Generated-answer citation completeness requires mapping generated claims to their
actual citations and validating support. The reference alignment alone cannot
label this. V1 stores the Gold needed for later review/judging and reports that
semantic metric as unavailable. It does not emit release pass/fail verdicts.

## Compatibility And Rollback

No production schema, Agent tools, ingestion, provider configuration or API
changes. No real-model calls and no third-party telemetry. Corpus text and answers
stay in explicitly requested local dataset artifacts; console summaries contain
IDs/counts/reason codes only. Tests must not depend on other in-progress tasks.

Rollback consists of removing the new evaluator package/entry point and its
local output directory; existing human/video datasets and runtime are untouched.
