# Implement: retrieval-agent budget

Implementer: `trellis-implement` sub-agent, model **sonnet**. Checker:
`trellis-check`.

## Safety (read first)

- **Never run pytest or anything that opens a DB connection without setting
  `DATABASE_URL` to the Neon test branch.** The repo `.env` points to
  PRODUCTION. Take the URL from `.env.neon-test` and assert the host is
  `ep-falling-fog-azrht8ir-pooler.c-3.ap-southeast-1.aws.neon.tech` and
  differs from the `.env` host. For example:
  `DATABASE_URL=<test url> .venv/bin/python -m pytest …` via a small wrapper
  that never prints the DSN.
- No DDL/DML except what the harness's normal operation does on the test
  branch.
- No git commit/add/stash/checkout/reset.
- Never block one tool call for more than ~5 min; background and poll.

## Ordered checklist

1. `app/agent/provider.py`: add `retrieval_model_settings` plus unit tests
   (DeepSeek → `extra_body` thinking disabled; non-DeepSeek → `thinking=False`;
   `parallel_tool_calls=False`).
2. `app/agent/orchestrator.py`: use it in `record_model_attempt`; emit
   `stage_usage` in a `finally` of `_run_primary_agent`.
3. `app/diagnostics.py`: add the `stage_usage` stage and its integer fields,
   with allow-list/sanitization tests in `tests/test_diagnostics.py`.
4. `app/agent/runtime_state.py` and `app/agent/agent_tools/policy.py`: remove
   the same-step gate per `design.md` §A. Rewrite the
   `tests/test_agent_runtime.py` same-step tests to assert:
   - two searches in one batch both execute;
   - a third search in the batch returns `budget_exhausted`;
   - three `get_neighbors` in one batch execute within the expansion budget;
   - empty-search recovery still requires a grant between sequential calls in
     one batch.
5. `app/agent/agent_builder.py`: rule 2 rewrite (`design.md` §C). Add tests:
   - after a search, a `FunctionModel` primary output of 「检索完成」 still
     yields the Composer answer;
   - a no-search capability/greeting answer is unchanged;
   - the todo finalize path works with the short text.
6. Spec updates listed in `design.md` §Boundary.
7. Targeted tests (with the `DATABASE_URL` override):
   `tests/test_agent_runtime.py tests/test_diagnostics.py tests/test_citation_first_provider_streaming.py tests/test_trusted_response_boundary.py tests/test_bounded_autonomy_runtime.py tests/test_answer_normalization.py`.
   Then run the full suite in the background with the override and compare
   with the baseline (32 failed + 1 collection error pre-existing).
8. Verification rerun (`design.md` §Verification run):
   - copy the harness to the scratchpad;
   - set `OUT_DIR` to a new scratchpad dir and copy
     `/private/tmp/claude-501/-Volumes-PeeB-projects-notebook-agent/65a84b49-1cb0-4e56-9c7a-28f3d0214a28/scratchpad/prodpath-postfix/ingest.json`
     into it first;
   - run in the background;
   - summarize with a comparison against the archived post-fix data, adding
     the `stage_usage` output tokens.

## Rollback

Revert `provider.py`, `orchestrator.py`, `diagnostics.py`, `runtime_state.py`,
`policy.py`, `agent_builder.py` and the tests.
