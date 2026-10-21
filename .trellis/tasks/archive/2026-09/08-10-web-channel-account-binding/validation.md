# Validation Report

## Automated Results

Implementation and review were performed by separate subagents.

- `pnpm test`: 15 files, 103 tests passed.
- `pnpm typecheck`: passed.
- `pnpm lint`: passed.
- `pnpm build`: passed.
- `pnpm check:api`: passed.
- Focused existing link-domain integration tests: 5 passed, covering expiry/replay, tenant merge,
  `link_merge_busy` token preservation, and `/link` command behavior.
- `tests/test_langbot_bridge_plugin.py`: 6 passed.
- `tests/test_http_gateway.py`: 5 passed.
- `git diff --check`: passed.

The broader bridge/http/multiuser command produced 26 passes and 3 failures in the unchanged
`tests/test_multiuser_integration.py`:

- `test_signed_save_actions_are_durable_and_exactly_once`
- `test_channel_delete_confirmation_chain_survives_real_clarification`
- `test_channel_new_is_blocked_while_delete_effect_applies_then_recovers`

All three reproduce when run alone as existing Agent-action failures/timeouts. This task has no diff
under `app/` or `tests/`, so they were not changed as part of this frontend task.

## Independent Review Finding

The review subagent found that generated and pasted link-token values would otherwise remain in the
TanStack MutationCache for its default garbage-collection window after leaving the page. Both sensitive
mutations now use `gcTime: 0`, and regression tests verify observer unmount removes the values.

## Manual Deployment Acceptance Still Required

The automated environment has no private Telegram Bot/LangBot credentials. Before production acceptance:

1. Create/configure the Telegram Bot in the private LangBot adapter settings.
2. Map the LangBot bot UUID to `telegram` in the installed bridge plugin's private
   `KB_BOT_CHANNELS`.
3. Verify required bridge readiness and run a redacted `/start` or `/whoami` private-chat smoke.
4. Run Web -> Telegram `/link <token>` and Telegram `/link web` -> Web flows.
5. Confirm both entry points reach the same private library without recording tokens, sender IDs, or
   message bodies in evidence.
