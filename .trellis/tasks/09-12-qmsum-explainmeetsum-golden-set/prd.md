# QMSum + ExplainMeetSum Golden Set

## Goal

Build a reproducible, locally usable meeting golden set combining QMSum
questions/reference answers with ExplainMeetSum sentence-level evidence.
The two sources enrich the same cases rather than count as separate datasets.

## Requirements

- Preserve English source questions, answers, meeting transcripts, query types,
  domains, and official train/val/test membership. Import all splits; reserve
  test for held-out evaluation and use val for development/smoke selection.
- Retain QMSum-only cases as answer-reference cases with unavailable evidence,
  separate from citation-scorable cases. Never invent annotations for them.
- Define mutually exclusive states per policy in an eligibility map:
  `citation_scorable` requires every evidence
  group to resolve and be non-empty; `partial_annotation` has unresolved groups;
  `answer_only` has no ExplainMeetSum annotation; `quarantined` has a hard failure.
  Report CES-only and CES+PES eligibility independently.
- Every scored evidence pointer must resolve to the correct meeting, turn,
  sentence, speaker, and source text. Retain the annotation-to-answer-sentence
  relationship and original evidence types.
- Freeze upstream commits, file hashes, converter/schema versions, source
  notices, selection policy, and generated artifact hashes.
- Provide deterministic import, validation, split/subset export, and offline
  evidence diagnostics. A clean build must produce actual data artifacts and
  an accounting report, not merely an empty schema or a sample template.
- Treat sentence-evidence overlap as an annotation coverage proxy, not proof
  of semantic support or generated-answer citation completeness.
- Keep corpus input separate from reference answers/evidence labels so an
  evaluated system cannot retrieve the answer key accidentally.
- Preserve existing video Gold schemas, 20-30-case human gates, runtime APIs,
  privacy boundaries, and unrelated worktree changes.

## Acceptance Criteria

- [ ] One documented command builds the full set from verified pinned inputs;
  the resulting manifest, corpus, split case files, audit report, and notices exist.
- [ ] Repeat builds from identical inputs produce identical data bytes/hashes;
  wrong revisions or modified source bytes are rejected.
- [ ] Counts reconcile at meeting, query, and annotation levels per split/domain:
  source = retained + explicitly excluded. No silent truncation or double import
  of domain copies/JSONL copies. Published 1,808 pairs are a check, not an assumption.
- [ ] Matched, unmatched, ambiguous, invalid, and partially annotated records
  are visible; excluded IDs and reasons are retained. No unresolved records
  enter the citation-scorable view.
- [ ] Matching is one-to-one within each meeting and query-list kind: every
  source record is consumed at most once, and unused records are audited.
- [ ] All Gold pointers and answer mappings pass structural/text validation;
  invalid indices, contradictory duplicate labels, cross-meeting references,
  and mismatched questions/answers fail validation or are quarantined.
- [ ] No meeting crosses train/val/test; cross-split identical transcript hashes
  are flagged without deleting source records; duplicates touching test block
  held-out certification. A deterministic val smoke view never draws from test.
- [ ] Missing evidence is unavailable, never a negative/no-evidence label;
  missing timestamps remain absent. No translations, synthetic seconds,
  generated reference claims, or invented negative cases are added.
- [ ] Offline diagnostics disclose numerator, denominator, eligible/omitted
  counts and evidence policy; unsupported semantic metrics return unavailable.
- [ ] Predictions bind to exact dataset and selection hashes. Complete mode
  requires every selected ID exactly once; missing records yield `incomplete`
  and unavailable aggregate scores.
- [ ] Answer index arrays are non-overlapping, ordered and contiguous; complete
  answer-text consumption detects omitted prefixes, suffixes and interior text.
  Any normalization needs source-backed review and a versioned policy.
- [ ] First freeze has a supervisor-approved split/domain/query/policy count and
  exception baseline. Later freezes reject unexpected changes. No represented
  split/domain has zero fully CES+PES-scorable cases in an accepted freeze.
- [ ] Focused tests and existing natural-language/video evaluation regression
  tests pass without paid calls, database changes, or network during unit tests.

## Scope And Approval

The user approved task creation, planning, and implementation on 2026-09-12.
The task is `in_progress`; implementation must stop for review on the explicit
conditions in the implementation plan.
Implementation was paused for document review and explicitly resumed by the user;
the supervisor owns contract clarification, audit approval and final verification.

This task delivers the offline golden-set package and scorer input contract.
Production ingestion, live Agent/MCP execution, semantic judges, release-gate
integration, model tuning, new video timestamps, and external dataset uploads
are out of scope. Integration with the in-progress evaluator release-gate task
can consume these frozen artifacts later without making this task depend on it.
