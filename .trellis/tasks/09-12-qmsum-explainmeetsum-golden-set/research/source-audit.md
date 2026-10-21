# Source And Integration Research

Inspected 2026-09-12 using read-only GitHub API requests. This is a planning
inspection, not a complete query-level audit or a generated golden set.

## Verified Upstream Evidence

- QMSum commit: `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`.
  Source: https://github.com/Yale-LILY/QMSum/tree/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e
- ExplainMeetSum commit: `d8459311a6578cd438a785bf19dd2fa757ade1c5`.
  Source: https://github.com/hkim-etri/ExplainMeetSum/tree/d8459311a6578cd438a785bf19dd2fa757ade1c5
- QMSum README reports 232 meetings and 1,808 query-summary pairs. Canonical
  meeting-file inventory independently counted train=162, val=35, test=35.
  Use only `data/ALL` per-meeting JSON, not the duplicated domain/JSONL copies.
- ExplainMeetSum canonical inventory counted train=161, val=35, test=35.
  README explicitly excludes `covid_1.json`; matched query counts still require
  full inspection. Repository metadata reports MIT for both sources; capture
  exact LICENSE bytes/notices in the implementation source lock.
- ExplainMeetSum format reference:
  https://github.com/hkim-etri/ExplainMeetSum/blob/d8459311a6578cd438a785bf19dd2fa757ade1c5/data/README.md
- QMSum general queries do not have relevant spans; specific queries may have
  several. These turn spans are distinct from sentence-level evidence labels.
- ExplainMeetSum evidence lives under `explainable_qmsum.general_query_list`
  and `specific_query_list`, not top-level query lists. Keep original answer
  groups, `answer_sentence_index` arrays and evidence `type` labels.
- Published `meeting_transcripts[].sentence_level_content` supplies indices,
  `dialogue_sentence` and optional `dialogue-act_id`. README describes NLTK
  segmentation for Committee and dialogue-act-based segmentation for ICSI/AMI.
  Rebuilding segmentation would risk changing evidence addresses.
- Inspected test examples: `Bed003.json` (Academic), `ES2004a.json` (Product),
  `covid_4.json` (Committee). All expose sentence-level evidence with CES/PES
  labels. Bed003 has one general and six specific queries; its initial QMSum
  query strings match the inspected ExplainMeetSum entries. This does not prove
  all answers/transcripts/annotations align throughout the dataset.
- Inspected raw transcript entries have speaker/content but no real timestamps.
  Stable sentence coordinates must not be represented as video seconds.

## Local Evidence

- `evals/natural_language/quality.py`: `HumanEvalDataset.samples` max_length=30;
  complete human gate requires 20-30 samples, six kinds, multi-segment and
  no-evidence cases. `GoldSample.is_gold_complete` requires time metadata.
- `score_gold_sample` computes group coverage with any matching segment. Its
  `citation_completeness` label must not be inherited as semantic truth for
  ExplainMeetSum's possibly jointly supporting sentences.
- `evals/natural_language/README.md`: real benchmark is paid, requires dedicated
  infrastructure/user and writes opt-in local review packages. Not needed for
  an offline dataset build.
- `.trellis/tasks/09-04-agent-evaluator-release-gate/design.md` describes planned
  freezing/comparison/privacy contracts. Its files are already dirty; it is
  reference context only and must not become an implementation dependency.
- Project helper directories `.agents/` and `.codex/` are absent, and the local
  skills root contains no `trellis-brainstorm/SKILL.md`. Follow the checked-in
  `.trellis/workflow.md` planning process directly.

## Open Implementation Checks

Count every query, label, empty group, merged sentence index and rejected join;
verify transcript/answer reconciliation, exact license text, full-file hashes,
cross-split transcript duplication, and policy-specific coverage. Do not promise
that all 1,808 queries will become fully citation-scorable or that CES/PES labels
alone establish generated-answer claim support.
