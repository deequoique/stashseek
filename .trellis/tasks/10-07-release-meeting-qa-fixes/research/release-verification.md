# Release verification: meeting-QA fixes on `release/meeting-qa-fixes`

Worktree: `/Volumes/PeeB/projects/notebook-agent-release`, branch
`release/meeting-qa-fixes`, created from `origin/main` `23a5d31`. All work was
done inside the worktree; the `dev` checkout (`/Volumes/PeeB/projects/notebook-agent`)
was never written to, except this one report file. No commit was made.

## 0. Scope correction applied mid-task

`evals/meeting_gold/` (RQ1 + agent-smoke harness, report), `tests/test_meeting_gold*.py`,
and `tests/fixtures/meeting_gold/` were initially copied into the worktree per
the original implement.md step 4 instruction. They were removed again before
finishing: that tree is untracked on `dev` too and belongs to the still-in-progress
task `09-12-qmsum-explainmeetsum-golden-set`, not to a shipped fix. They were
used only as the harness for the step-7 production-path rerun (which imports
`evals.meeting_gold.*`) and then deleted from the worktree. The quality report
will instead be committed under
`.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/` with the
Trellis records, not in the release branch.

Current worktree diff is exactly:

```
$ git -C /Volumes/PeeB/projects/notebook-agent-release diff origin/main --stat
 .env.example                                            |   2 +-
 .trellis/spec/backend/agent-retrieval-convergence.md    | 227 +++++++-
 .trellis/spec/backend/browser-companion-capture.md      |   2 +-
 .trellis/spec/backend/database-guidelines.md            |  95 ++++
 .trellis/spec/backend/index.md                          |   1 +
 app/agent/agent_builder.py                              |  13 +-
 app/agent/agent_tools/policy.py                         |  17 +-
 app/agent/answer_pipeline.py                            | 273 ++++++++--
 app/agent/orchestrator.py                               | 580 +++++++++++----------
 app/agent/provider.py                                   |  20 +
 app/agent/response.py                                   |  13 +-
 app/agent/runtime_state.py                              |  16 +-
 app/agent/types.py                                      |  26 +-
 app/config.py                                           |   2 +-
 app/diagnostics.py                                      |  18 +-
 app/ingest/chunker.py                                   | 270 +++++++---
 app/ingest/embed.py                                     | 121 ++++-
 app/ingest/tasks.py                                     |  10 +-
 app/models.py                                           |   7 -
 app/retrieval/search.py                                 | 144 ++++-
 docs/explanation/ingestion-and-retrieval.md              |  37 +-
 docs/reference/configuration.md                          |   2 +-
 tests/test_agent_runtime.py                              | 349 +++++++++++--
 tests/test_bounded_autonomy_runtime.py                  | 156 +++++-
 tests/test_chunker.py                                    | 174 ++++++-
 tests/test_citation_first_provider_streaming.py          | 161 +++++-
 tests/test_diagnostics.py                                 |  24 +
 tests/test_embed.py                                      | 161 ++++++
 tests/test_exact_video_reference_routing.py               |   2 +-
 tests/test_provider_and_explicit_user.py                  |  33 +-
 tests/test_tasks.py                                       |  79 +++
 tests/test_trusted_response_boundary.py                   |  18 +
 tests/test_web_auth_migration.py                          |   5 +-
 33 files changed, 2485 insertions(+), 573 deletions(-)
```

Plus 3 new (untracked) files: `.trellis/spec/backend/ingestion-chunking-embedding.md`,
`migrations/versions/d04fdae36884_drop_segment_embedding_hnsw.py`,
`tests/test_vector_search_filter_first.py`.

`git diff origin/main --stat` lists no WIP-only path (no `app/agent/media*`,
no `MediaAsset`/`MediaEmbedding`, no `wemm_local`, no web-frontend-removal or
extension-rename files, no `c9d0e1f2a3b4` media migration).

## 1. Baseline vs release test results

Both runs used the release worktree's interpreter
(`/Volumes/PeeB/projects/notebook-agent/.venv/bin/python -m pytest`) with
`DATABASE_URL` forced to the Neon **test** branch host
`ep-falling-fog-azrht8ir-pooler.c-3.ap-southeast-1.aws.neon.tech` (asserted
different from the production host `ep-royal-cherry-auyrb0bt-pooler...` on
every invocation; no DSN ever printed). The wrapper script is
`/private/tmp/claude-501/.../scratchpad/run_tests_testdb_release.sh` (session
scratchpad, mirrors `data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh`
but `cd`s into the worktree).

| | Baseline (untouched `origin/main` in the worktree) | Release (all fixes ported) |
|---|---|---|
| Result | **11 failed, 839 passed, 9 skipped** (85.86s) | **11 failed, 879 passed, 9 skipped** (87.56s) |

Failing tests, by name:

```
Baseline (11):
  test_agent_runtime.py::test_agent_rejects_model_answer_that_skips_retrieval
  test_item_management_tools.py::test_delete_restore_and_resave_converge_on_same_item
  test_item_management_tools.py::test_save_restore_early_return_is_committed_and_retry_queue_failure_is_retryable
  test_multiuser_integration.py::test_agent_tool_uses_real_pgvector_and_hydrates_only_tenant_citation
  test_multiuser_integration.py::test_signed_save_actions_are_durable_and_exactly_once
  test_multiuser_integration.py::test_unrelated_question_keeps_live_pending_action_unchanged
  test_provider_and_explicit_user.py::test_action_services_are_always_composed
  test_web_api_app.py::test_unhandled_request_failure_is_contained_without_private_traceback
  test_web_auth_channel.py::test_web_login_diagnostics_keep_the_safe_public_failure_code
  test_web_auth_channel.py::test_web_login_emits_safe_request_diagnostics
  test_web_auth_postgres.py::test_challenge_persists_only_hashes_and_exchanges_once

Release (11):
  test_item_management_tools.py::test_delete_restore_and_resave_converge_on_same_item
  test_item_management_tools.py::test_save_restore_early_return_is_committed_and_retry_queue_failure_is_retryable
  test_migration_roundtrip_postgres.py::test_agent_action_migration_upgrade_downgrade_upgrade_isolated   <-- NEW
  test_multiuser_integration.py::test_agent_tool_uses_real_pgvector_and_hydrates_only_tenant_citation
  test_multiuser_integration.py::test_signed_save_actions_are_durable_and_exactly_once
  test_multiuser_integration.py::test_unrelated_question_keeps_live_pending_action_unchanged
  test_provider_and_explicit_user.py::test_action_services_are_always_composed
  test_web_api_app.py::test_unhandled_request_failure_is_contained_without_private_traceback
  test_web_auth_channel.py::test_web_login_diagnostics_keep_the_safe_public_failure_code
  test_web_auth_channel.py::test_web_login_emits_safe_request_diagnostics
  test_web_auth_postgres.py::test_challenge_persists_only_hashes_and_exchanges_once
```

- **10 failures are identical** in both runs (pre-existing on `origin/main`,
  unrelated to this release: `OPENAI_API_KEY` not set in this environment for
  `test_action_services_are_always_composed`, and web-auth/session-timing
  issues in the others). Verified by re-running them directly against the
  unmodified worktree before any fix was ported.
- **`test_agent_runtime.py::test_agent_rejects_model_answer_that_skips_retrieval`
  moved from failing to passing.** This is the intended effect of the
  10-07-retrieval-agent-budget orchestrator fix (a model that never calls
  `search_segments` for a content question must fail closed with
  `search_required`); it was failing on bare `origin/main` because main's
  no-search gate only checked the legacy `reference_scope` field, which the
  normal web/channel request path never populates.
- **One new failure**:
  `test_migration_roundtrip_postgres.py::test_agent_action_migration_upgrade_downgrade_upgrade_isolated`.
  Root cause (confirmed, not a release-port artifact — see §2 adaptation A11):
  the already-approved `10-06-tenant-vector-filter-first` migration
  `d04fdae36884` uses `op.get_context().autocommit_block()` (required for
  `DROP INDEX CONCURRENTLY`). This test drives `alembic` with one externally
  attached `Connection` it opened itself
  (`alembic_config.attributes["connection"] = connection`) rather than
  letting `command.upgrade/downgrade` own the connection lifecycle; Alembic's
  `autocommit_block()` asserts `self._transaction is not None` in that mode,
  which fails for a migration positioned where this test's chain crosses it.
  **I reproduced the identical failure on the full, unmodified `dev` working
  tree** (which already has `d04fdae36884` plus the WIP `c9d0e1f2a3b4` media
  migration after it) — same `AssertionError` in
  `alembic/runtime/migration.py:329`, same point in the chain. This is a
  pre-existing defect in the fix's own migration test fixture that nobody
  exercised before now (no earlier task ran the *generic* roundtrip test
  past this migration), not something the release-port adaptation introduced
  or could fix without changing test-harness connection-management logic,
  which is outside this task's porting mandate. **Flagging for your decision**:
  options are (a) ship with this one documented failure and fix the test
  harness in a follow-up task, (b) have me change the test to manage its own
  per-migration connection/transaction (more invasive, I did not do this
  unilaterally), or (c) mark it `xfail` with a comment. I did not pick for
  you.

`alembic heads` on the worktree: **`d04fdae36884` (head)** — exactly one head,
matching R4.

## 2. Every adaptation (WIP dependency removed or code/test corrected)

All code hunks were taken from `git -C <dev> diff origin/main -- <file>`,
read file-by-file, and applied only where they had no WIP dependency. Each
row below is a case where the dev-tree diff needed adjustment before it was
usable on main's code, or where a test needed a genuinely new edit beyond the
dev tree's own diff.

| # | File | WIP dropped / adaptation | Why |
|---|---|---|---|
| A1 | `app/agent/runtime_state.py` | Dropped `app.agent.media` imports, `UnifiedRetrievalResult`, `MultimodalEmbeddingProvider`; dropped `media_manifest`, `vision_service`, `media_inspection_cache`, `media_evidence_adapter`, `media_evidence` (both `AgentDeps` and `ComposerDeps`), `media_inspection_calls`, `active_tenant_id`, `active_retrieval_run_id`, `multimodal_embedding_provider`, `multimodal_retrieval_enabled`, `last_retrieval_result`, `reserve_media_inspection`. Kept: `COMPOSER_EVIDENCE_EXCERPT_CHARS = 1200`, the `SAME_STEP_SKIPPED` compat comment, removal of `last_retrieval_run_step`, the same-step gate removed from `reserve_retrieval`. | Multimodal/media fields and the media inspection budget are the in-progress `09-xx` media/WeMM feature; the fix only needs the excerpt cap and the budget-gate removal. |
| A2 | `app/agent/answer_pipeline.py` | Dropped `MediaCitation`/`AgentAnswer` imports used only for the media-citation answer-assembly branch, `_draft_has_media`, the `media_evidence` param on `_render_composer_evidence`, the whole `selected_media`/`MediaCitation`/`media_evidence_adapter.recheck` block in `recover_answer`, and the `_COMPOSER_GLOBAL_CONSTRAINTS`/`COMPOSER_INSTRUCTIONS` wording changes that mention `media_citation_ids`/`[M…]`/HTML. Kept: `NormalizationReport`/`normalize_section_citations`/`_dedupe_citations_by_segment` (pure, text-only), the stream-plan and composer normalization wiring, `_draft_scope_failure_reason` split out of `_draft_failure_reason`, the whitespace-guard fix in `_StreamingTextGuard._has_forbidden_text`, and the 3 `agent_output_token_limit` → `agent_composer_max_tokens` call-site changes (decouples the Composer's own token budget from the retrieval-stage limit raised in A9; `agent_composer_max_tokens` already existed on `main`, unused by these call sites). | Media citations are the in-progress multimodal feature; the citation-normalization fix (duplicate/over-cap round-robin) and the whitespace-stream fix apply to plain segment citations only and have no media dependency. |
| A3 | `app/agent/types.py` | Dropped `UnifiedRetrievalCandidate`/`UnifiedRetrievalResult` dataclasses, `MediaCitation` model, `media_citation_ids` on `GroundedSection` (and its validator branches), `media_citations` on `AgentAnswer`. Kept: `RetrievalToolPayload.reason` narrowed to `Literal["budget_exhausted"]`, `AnswerDraft.sections_note` (DeepSeek-quirk tolerance field — explicitly called out in the PRD as a "types.py relaxation"), and the duplicate-citation-id checks removed from `PlannedSection`/`AnswerStreamPlan` validators (normalization now owns this, per decision F1). | Same media/multimodal boundary as A1/A2; the schema relaxations the PRD calls out are independent of media. |
| A4 | `app/agent/response.py` | No WIP; applied dev's diff verbatim. | Pure dedup/order-of-checks fix for the "same segment in two sections" case (F1); nothing media-related touches this file. |
| A5 | `app/agent/orchestrator.py` | Dropped `MediaCitation`, `MultimodalEmbeddingProvider`, `MediaRetrievalService`/`compose_media_answer`/`render_media_answer` imports; dropped the `multimodal_embedding_provider`/`media_search_service`/`media_evidence_adapter`/`vision_service` constructor params and the matching `self._...` fields; dropped `build_agent(..., multimodal_retrieval_enabled=..., media_search_service=...)` (kept the 2-arg call); dropped `active_tenant_id`/`active_retrieval_run_id`/`multimodal_embedding_provider`/`multimodal_retrieval_enabled`/`media_evidence_adapter`/`vision_service` from the `AgentDeps(...)` construction; dropped the whole `compose_media_answer` branch and the media-manifest no-evidence branch in `_finalize_primary_result`; dropped `_recheck_media_evidence`/`_recheck_selected_media`; dropped `not deps.media_evidence` from the `streamable` check (kept `bool(deps.citations and isinstance(natural_text, str))` since `AgentDeps` carries no `media_evidence` field in this port). Kept: `retrieval_model_settings` import and `self._retrieval_model_settings` wired once in `__init__`; `record_model_attempt` returning `dict(self._retrieval_model_settings)`; the `_is_no_search_social_or_capability` helper; the `no_search_allowed` rewrite of the `deps.search_calls < 1` gate; the outer `try/finally` wrapping the whole retrieval-phase try/except so `stage_usage` is emitted on every exit path (success, timeout, limit, embedding/retrieval-unavailable, `ModelHTTPError`, `UnexpectedModelBehavior`, `KnowledgeNotFound`, generic `Exception`). | The file mixes the approved retrieval-budget fix with the in-progress multimodal answer path end-to-end; every dropped symbol belongs only to the media feature (confirmed by grep: none of them are referenced anywhere else in the ported code). |
| A6 | `app/agent/agent_builder.py` | Dropped `multimodal_retrieval_enabled`/`media_search_service` params, the `media_retrieval_instruction` function, and the `register_retrieval_tools(..., multimodal_retrieval_enabled=..., media_search_service=...)` call (kept the 2-arg call). Kept: the rule-2 prompt rewrite (post-search "检索完成" completion contract; no-search categories unchanged). | `inspect_media`/`search_media` tool wiring is the media feature; the prompt-contract rewrite itself has no tool dependency. |
| A7 | `app/agent/agent_tools/policy.py` | Dropped `prepare_media_inspection`. Kept: `skipped_payload` simplified to always return `reason: "budget_exhausted"` (the `same_model_step` branch and its now-unused `Literal` import removed). | `prepare_media_inspection` gates the (not-yet-existing) `inspect_media` tool; the skip-reason simplification is the direct Issue-A budget fix. |
| A8 | `app/ingest/embed.py` | Dropped `EmbeddingSpaceMetadata`, `MediaObjectReference`, `MultimodalInput`, `MultimodalEmbeddingProvider` Protocol, `EmbeddingInputError`, and the `__getattr__` lazy `WemmLocalEmbedder`/`WemmEmbedder` loader. Kept `EmbeddingError.__init__(self, message, *, code="embedding_error")` (needed — `tests/test_embed.py::test_embed_normalizes_huge_integer_values_to_safe_embedding_error` asserts `caught.value.code == "embedding_error"`, and that test is part of the embed-retry fix, not WIP), `_normalize_vector`, and the whole classified-retry `_fetch_batch_response`/`_is_transient_error`/`_retry_delay_seconds` machinery. | `EmbeddingInputError` and the multimodal dataclasses/Protocol exist only to support `app/ingest/wemm_local.py` (confirmed by grep — nothing else references them); the retry/classification logic they were bundled next to is the actual fix and has no multimodal dependency. |
| A9 | `app/config.py` | Ported only the single line `AGENT_OUTPUT_TOKEN_LIMIT` default `2000`→`3000`. Left untouched: the entire `_WemmEnv` class, `wemm_*`/`multimodal_retrieval_enabled` fields, `_validate_wemm`, the `WEB_STATIC_DIR` default rename (`web/dist`→`extension/dist`, part of the frontend-removal WIP, out of scope). | Everything else in the dev-tree `config.py` diff is WIP (WeMM settings, extension static-dir rename). |
| A10 | `app/models.py` | Removed only the `Index("ix_segment_embedding_hnsw", ...)` entry from `Segment.__table_args__`. Did **not** port `MediaAsset`/`MediaEmbedding` ORM classes, the `ForeignKeyConstraint`/`UniqueConstraint("id","user_id")` added to `ContentItem` to support the media FK, or `ImportError`-free `ForeignKeyConstraint` import (not needed once media models are dropped). | Matches the PRD row verbatim: "model HNSW declaration removed (segment only — the media model does not exist on main)". |
| A11 | `evals/meeting_gold/agent_smoke.py` (used transiently, then removed — see §0) | Dropped the `multimodal_embedding_provider=None` kwarg on `KnowledgeAgent(...)`, since the release `KnowledgeAgent.__init__` has no such parameter. | Would have raised `TypeError` otherwise; this module's only WIP touchpoint. |
| A12 | `migrations/versions/d04fdae36884_drop_segment_embedding_hnsw.py` | None — copied verbatim. `down_revision = "b8c9d0e1f2a3"` already equals main's head, so no re-parenting was needed (the dev tree's WIP chains `c9d0e1f2a3b4` after it instead). | Self-contained, no WIP dependency. |
| A13 | `.trellis/spec/backend/agent-retrieval-convergence.md` | Applied the dev diff, then removed 4 media-only phrases that don't apply without the media feature: "`search_media`" from the retrieval-tool list in the post-search completion-discard contract; the "A draft that mixes media citations with segment citations keeps the prior fail-closed ... behavior; media citations are not normalized" sentence; the "a draft mixes media and segment citations ..." row in the error matrix; "and media-mixed drafts" from the tests-required bullet. Kept the two generic "segment embeddings, media embeddings, or any future embedding column" mentions in the new filter-first-vector-search scenario (parallel to the already-generic phrasing in `database-guidelines.md`, which the PRD explicitly scopes in). | The spec otherwise describes exactly the ported behavior (budget, completion-discard, citation normalization, filter-first vector search); only the mixed-media-citation carve-out doesn't exist in this port. |
| A14 | `tests/test_agent_runtime.py` | Applied the dev diff, then (a) removed `"inspect_media"` from the expected tool-schema set in `test_model_tool_schemas_never_expose_trusted_identifiers` (that tool doesn't exist in this port), and (b) reverted the `_COMPOSER_GLOBAL_CONSTRAINTS` substring assertions in `test_answer_agent_feedback_covers_unparseable_first_attempt` back to main's original wording ("grounded section 必须有非空 text 和 citation_ids" / "不得包含 URL、来源块或 [S…] 标记"), since A2 did not port the media-aware prompt wording. | Keeps the test in sync with the code actually shipped. |
| A15 | `tests/test_bounded_autonomy_runtime.py` | Applied the dev diff (new post-search-completion tests, unchanged). **Additionally rewrote** the pre-existing, dev-tree-unmodified test `test_flag_on_explicit_url_question_cannot_finish_without_search`: old assertion was `status == "ok"` / `error_code is None` for a URL-plus-content question answered without any search call; new assertion is `status == "failed"` / `error_code == "search_required"`. | Required by A5 (`no_search_allowed` gate): a URL-plus-content question is not a greeting/capability/clarification/management-read, so it must now fail closed. This is the exact same bug class the dev tree itself already fixed in the sibling test below (A16) — the dev tree just never updated this second, duplicate assertion in a different test file. Verified: this test fails identically on the full, unmodified `dev` working tree (not a release-port artifact), and passes after the same 2-line fix there. |
| A16 | `tests/test_exact_video_reference_routing.py` | No WIP; applied dev's own 1-line diff verbatim: `test_management_tools_are_hidden_for_explicit_url_questions` now expects `error_code == "search_required"` instead of `None`. | Direct test for the same A5/no-search-gate fix; this one dev did update. |
| A17 | `tests/test_trusted_response_boundary.py` | No WIP; applied dev's own diff verbatim (`sections_note` null/non-null `AnswerDraft` cases). | Direct test for A3's `sections_note` relaxation; PRD explicitly lists "types.py / response.py relaxations" for this fix. |
| A18 | `tests/test_web_auth_migration.py` | Not in the dev tree's own diff (dev tree has **no** changes to this file; it is equally broken there, just against the wrong head `c9d0e1f2a3b4`). Updated `test_web_and_ingest_completion_branches_converge_on_one_merge_head`'s hardcoded single-head assertion from `b8c9d0e1f2a3` to `d04fdae36884`, and added the one new `down_revision` assertion for the new head. | Mechanical, required consequence of accepting migration `d04fdae36884` (R4: "exactly one Alembic head (`d04fdae36884`)"); the alternative is to leave this test broken too. |
| A19 | `tests/test_vector_search_filter_first.py` | None — new file, copied verbatim (one stray comment mentions "a future embedding table (e.g. media)" generically, no actual dependency). | Self-contained unit test for the filter-first helper. |
| A20 | `tests/test_embed.py` | Did not apply the dev diff's `build_multimodal_embedding_provider`/`Settings(wemm_enabled=...)` tests or the `test_existing_zhipu_adapter_remains_the_feature_off_text_provider` test. Manually added only the embed-retry test functions (`_RecordingSleep`/`_ZeroJitterRandom` helpers, the 7 retry/classification tests) plus the one huge-integer normalization test, matching the embed-retry PRD's acceptance criteria exactly. | WeMM/multimodal settings tests are out of scope; the retry tests have no WIP dependency. |
| A21 | `.env.example`, `docs/reference/configuration.md` | Ported only the single `AGENT_OUTPUT_TOKEN_LIMIT` line each (2000→3000, with the explanatory comment in the docs table). | Matches the PRD's explicit scope: "the single `AGENT_OUTPUT_TOKEN_LIMIT` lines". |

No other test file required a change beyond what's listed above and in the
PRD table; `tests/test_knowledge_services.py` and `tests/test_multiuser_integration.py`
have **zero** diff from `origin/main` in the dev tree (the filter-first fix's
PRD lists them as "must pass", not "must change") and were left completely
untouched — confirmed green except for the 3 pre-existing
`test_multiuser_integration.py` failures that are also broken on bare
`origin/main` (missing `OPENAI_API_KEY` in this environment, unrelated to the
fix).

## 3. Production-path 30-case rerun

### Harness

Copied `data/meeting_gold/runs/2026-10-specific30/scripts/meeting_agent_budget_rerun.py`
(the PRD-suggested non-"thinkingon" variant — it already includes the
`stage_usage` tracking the budget task needs; the `_thinkingon` variant only
adds a diagnostic override to `{"parallel_tool_calls": False}`, which is
already the production value under decision B1, so it's a no-op either way)
to the scratchpad as `meeting_agent_release_rerun.py`:

- `sys.path.insert(0, str(WORKTREE_ROOT))` so `app`/`evals` resolve from the
  release worktree; asserted at import time
  (`assert _app_path.startswith(str(WORKTREE_ROOT.resolve()))`) — verified in
  every run's first stdout line, e.g.
  `{"stream_model": true, ..., "app_file": "/Volumes/PeeB/projects/notebook-agent-release/app/__init__.py"}`.
- `.env`/`.env.neon-test` (production model/embedding settings, test-branch
  `DATABASE_URL`) and the meeting_gold dataset (`data/meeting_gold/final`,
  gitignored, absent from the worktree) still load from the **dev checkout**.
- Dropped `multimodal_embedding_provider=None` from the `KnowledgeAgent(...)`
  call and the `settings.multimodal_retrieval_enabled` print field (neither
  exists in this port).
- `OUT_DIR` pointed at a fresh scratchpad directory; the post-fix
  `03-prodpath-postfix-20261006/ingest.json` (test-branch tenants/items
  99-118, verified present and `state='ready'` with 1,349 segments via a
  read-only `SELECT` before running) was copied in first — **no re-ingestion
  occurred**.

### Result

30 cases, one subprocess per case (as the harness does), ~250-ish LLM calls
(within the pre-approved budget) plus a small number of retries investigated
below.

| Metric (n=28 eligible unless noted) | Reference `05-budget-final-thinking-on-20261007` | Release, first pass (one attempt/case) | Release, effective (first pass + 1 retry for the 7 noisy failures) |
|---|---|---|---|
| Answered | 30/30 | 23/30 | **30/30** |
| Failure categories (first attempt) | none | `timeout`×1 (harness subprocess timeout, no in-app error), `answer_unavailable`×2, `runtime_error`×3 | `answer_unavailable`×1 before the single retry that fixed it too (see below) |
| Pool gold hit@5 | 27/28 = 0.964 | 24/24 = 1.0 | 28/28 = 1.0 |
| Mean pool recall | 0.780 | 0.789 | 0.785 |
| Mean cited-gold recall | 0.585 | 0.654 | 0.604 |
| Cases with an output-token limit hit | 0 | 1 (`Bed002:5`, completed anyway via bounded recovery) | 1 (same case) |
| Tool calls executed / skipped | 94 / 3 | 82 / 3 | 104 / 3 |
| Total provider (chat-completions) calls | 176 | 142 | 178 |
| Retrieval-stage `output_tokens` median / max | 432.5 / 1975 | 450 / 3656 | 480.5 / 3656 |
| Median latency (s) | 9.15 | 9.3 | 10.6 |

All pool/recall/latency/token figures are close to the reference run and show
no systematic regression; the one max-output-tokens outlier (3656, case
`Bed002:5`) reflects more tool calls in that particular non-deterministic
retrieval trace (6 vs the reference's fewer), not a code defect — the turn
still completed successfully via the existing bounded answer-recovery path.

### Investigation of the 7 first-pass failures (none are porting defects)

| Case | First-attempt symptom | Diagnosis | Retry outcome |
|---|---|---|---|
| `Bed015:2`, `Bmr026:0`, `Bro005:3` | `runtime_error`, ~1.7s elapsed, diagnostics show `error_class: ModelAPIError`, `exception_message: "Connection error."` at the very first `model_attempt` | Transient network failure to the chat-completions provider (DeepSeek), not the embedding provider the A8 retry fix targets. No code in this release path retries the primary agent's own provider call (by design — only `app/ingest/embed.py` retries, and only for ingestion-time embedding batches). | All 3 succeeded on a single retry (8/8 citations recovered, `hit@5=1.0`/`0.0` depending on gold set, no errors). |
| `Bro022:3`, `ES2006b:3` | `harness_timeout` (the harness's own `subprocess.run(..., timeout=420)` killed the child) | Reproduced one of these (`Bro022:3`) directly with `faulthandler.dump_traceback_later(60s)` armed outside the harness's subprocess wrapper: it completed cleanly in 18.2s with **no** stack dump fired, i.e. no hang. This points to transient system/network contention during the full 30-case run (this session had several pytest suites, the rerun, and a parallel diagnostic process competing for CPU/network at the same time), not a deadlock in the ported code. | Both succeeded on a single retry (8/8 citations, `hit@5=1.0`/`0.0`). |
| `Bro011:3` | `answer_unavailable`; `stream_failures: [{"exception": "NaturalAnswerValidationError", "message": "unsafe streamed text"}]`; `guard_rejections: [{"rule": "answer must not contain a source block"}]` | **Not** the ported whitespace-guard fix (A2): that fix only whitelists whitespace-only deltas; a genuine "source block" pattern in model-generated text is still, correctly, rejected by `validate_natural_answer` — this is working as designed, pre- and post-fix. The model occasionally emits a source-block-like pattern in composer section text; this is sampling noise at the provider, not a validation-logic bug. Confirmed against the reference run: this exact case (`Bro011:3`) succeeded there with 8 citations, i.e. it's genuinely non-deterministic case-by-case. | Retried **4 times** (3 reusing the pinned test-branch item plus once via a fresh same-meeting re-ingest as an extra check): **succeeded all 4 times** (8 citations, `hit@5=1.0`, no guard rejections). |
| `ES2009c:0` | Same symptom/diagnosis as `Bro011:3` (`guard_rejections: "answer must not contain a source block"`) | Same as above — this is one of the two cases the 10-07-retrieval-agent-budget PRD itself already flagged as sensitive (`Bed015:2`/`ES2009c:0` under thinking-off; thinking is on in this release, so this is unrelated sampling noise in the composer stage, not the retrieval-skip issue B1 fixed). | Succeeded on a single retry (8 citations, no guard rejections). |

Net: **30/30 answered** once the 7 non-deterministic first-attempt failures
are retried once each, all 7 succeeding; no evidence of a porting defect in
the whitespace-stream-guard, citation-normalization, or budget code paths.

## 4. Proposed commit plan

All commits are staged against `release/meeting-qa-fixes`, in PRD-table
order, specs/docs/tests alongside their code. **Not yet committed** — shown
here for your one-shot confirmation per R3.

1. **`fix: semantic-first chunking with bounds, overlap, and excerpt cap`**
   `app/ingest/chunker.py`, `app/ingest/tasks.py`, `app/agent/runtime_state.py`
   (excerpt-cap line only — the rest of that file's changes belong to commit
   4), `tests/test_chunker.py`, `tests/test_tasks.py`,
   `.trellis/spec/backend/ingestion-chunking-embedding.md`,
   `.trellis/spec/backend/index.md`,
   `.trellis/spec/backend/browser-companion-capture.md`,
   `docs/explanation/ingestion-and-retrieval.md`.
   *(Note: `runtime_state.py`'s excerpt-cap constant and the budget fix's
   `reserve_retrieval`/`SAME_STEP_SKIPPED` edits are both in one file; since
   Trellis tracks per-file not per-hunk commits, I recommend folding the
   whole file into commit 4 instead and dropping it from this commit — see
   the file list actually proposed below.)*
   - Files: `app/ingest/chunker.py`, `app/ingest/tasks.py`,
     `tests/test_chunker.py`, `tests/test_tasks.py`,
     `.trellis/spec/backend/ingestion-chunking-embedding.md`,
     `.trellis/spec/backend/index.md`,
     `.trellis/spec/backend/browser-companion-capture.md`,
     `docs/explanation/ingestion-and-retrieval.md`.

2. **`fix: normalize answer citations and stop fail-closed on whitespace streams`**
   - Files: `app/agent/answer_pipeline.py`, `app/agent/types.py`,
     `app/agent/response.py`, `app/diagnostics.py`,
     `tests/test_agent_runtime.py`, `tests/test_citation_first_provider_streaming.py`,
     `tests/test_trusted_response_boundary.py`, `tests/test_diagnostics.py`.
     *(`test_agent_runtime.py` and `test_diagnostics.py` carry hunks shared
     with commit 4's budget fix — both are additive and don't conflict; if
     you want strictly disjoint commits I can split by hunk instead of by
     file, at the cost of a messier history.)*

3. **`fix: retry transient embedding batch failures`**
   - Files: `app/ingest/embed.py`, `tests/test_embed.py`.

4. **`fix: filter-first tenant vector search; drop global segment HNSW index`**
   - Files: `app/retrieval/search.py`, `app/models.py`,
     `migrations/versions/d04fdae36884_drop_segment_embedding_hnsw.py`,
     `tests/test_vector_search_filter_first.py`,
     `.trellis/spec/backend/database-guidelines.md`.

5. **`fix: execute every retrieval call within stage budgets; discard post-search text; raise output-token limit`**
   - Files: `app/agent/runtime_state.py`, `app/agent/agent_tools/policy.py`,
     `app/agent/provider.py`, `app/agent/orchestrator.py`,
     `app/agent/agent_builder.py`, `app/config.py`, `.env.example`,
     `docs/reference/configuration.md`,
     `tests/test_bounded_autonomy_runtime.py`,
     `tests/test_exact_video_reference_routing.py`,
     `tests/test_web_auth_migration.py`, `tests/test_provider_and_explicit_user.py`.
     *(`test_web_auth_migration.py` is only here because accepting
     migration `d04fdae36884` in commit 4 changes the single-head value this
     test asserts; it could instead go in commit 4 — your call.)*

6. **`chore: spec contract update for budget + citation normalization + filter-first vector search`**
   - File: `.trellis/spec/backend/agent-retrieval-convergence.md` (covers all
     three contract changes — completion-discard, citation normalization,
     filter-first vector search — in one file; could be split per-commit
     above instead if you prefer one spec hunk per code commit).

7. **`chore: Trellis task records and release verification report`**
   - Files: this task's `.trellis/tasks/10-07-release-meeting-qa-fixes/*`
     records plus `research/release-verification.md` (this file), and the
     `10-06-tenant-vector-filter-first` task's closure if applicable.
     *(The `evals/meeting_gold` quality report mentioned in the original PRD
     table is **not** part of this branch per §0 — it belongs to
     `10-06-meeting-qa-quality`'s archive, not here.)*

Decisions needed from you before I commit:
- Commit granularity: file-level commits as listed (some files carry two
  fixes' hunks, e.g. `runtime_state.py`, `agent_runtime.py` tests) vs.
  splitting by hunk for a cleaner 1:1 fix↔commit mapping.
- What to do about the one new test failure
  (`test_migration_roundtrip_postgres.py::test_agent_action_migration_upgrade_downgrade_upgrade_isolated`,
  §1) — ship as a known/documented failure, fix the harness, or `xfail` it.
- Whether `evals/meeting_gold`'s quality report should be committed to the
  `10-06-meeting-qa-quality` archive as part of this same session (I did not
  touch anything under `.trellis/tasks/archive/` other than reading it).

## 5. Post-report updates (main session, 2026-10-07)

- The user chose a plain transactional migration. `d04fdae36884` now uses
  `DROP INDEX IF EXISTS` / `CREATE INDEX IF NOT EXISTS` in both trees, and
  `tests/test_migration_roundtrip_postgres.py` passes.
- Schema check on a temporary empty database on the Neon test branch, run from
  the release worktree:
  - `alembic heads` = `d04fdae36884`;
  - `upgrade head` from base through the full chain succeeded;
  - `current` = `d04fdae36884`;
  - `check` reported no new operations;
  - no HNSW index and no media tables;
  - the temporary database was dropped.
- `trellis-check` passed. It made one fix: the stale `search_media` mention in
  rule 2 of the release prompt was removed, and the regression test
  `test_bounded_autonomy_instructions_never_name_an_unregistered_tool` was
  added. Full suite: 10 failed (the identical pre-existing set; the origin/main
  baseline had 11), 881 passed, 9 skipped.
- Commit grouping chosen by the user: 6 commits by fix, with a file touched by
  several fixes going into the latest of them, plus the Trellis records and
  the report.
