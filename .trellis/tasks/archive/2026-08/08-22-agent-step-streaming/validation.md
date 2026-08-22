# Validation Report

## Outcome

The safe Agent execution timeline is implemented across the primary runtime,
internal events, browser SSE contract, generated API types, strict client
parser, transient React state, accessible timeline UI, documentation, and
code-spec.

## Focused backend verification

```text
145 passed, 1 deselected in 6.00s
```

The focused set covers conversation streaming, Citation-first provider
streaming, trusted-response boundaries, item-management tools, Agent actions,
primary runtime events, multi-call batches, early consumer cleanup, EOF/open
step closure, replay exactly-once persistence, and disconnect/no-partial-turn
persistence.

The deselected test is
`test_agent_rejects_model_answer_that_skips_retrieval`. It is already documented
by archived task `08-22-evidence-first-routing-agent-self-knowledge` as an
obsolete zero-search assertion after the product deliberately removed the
server fallback. The implementation does not change the non-streaming branch
that this test exercises.

## Full Python suite

```text
737 passed, 73 skipped, 7 failed, 5 errors
```

Unrelated baseline/environment failures:

- five demo API errors and two HTTP gateway failures cannot bind loopback ports
  under the managed sandbox (`PermissionError: operation not permitted`);
- one obsolete `search_required` assertion described above;
- four existing Settings tests inherit incompatible environment values such as
  wildcard extension origins or development-only retrieval logging and fail
  before reaching their intended assertion.

No full-suite failure enters the changed Agent stream, SSE step, OpenAPI, client
parser, or timeline component paths.

## Frontend verification

```text
18 test files passed
137 tests passed
TypeScript passed
ESLint passed with zero warnings
Vite production build passed
OpenAPI export check passed
openapi-typescript generated-schema check passed
```

The in-app browser could not access the local preview because its enforced
security policy could not authorize the localhost target. No bypass was used.
Responsive layout, disclosure behavior, reduced motion, and accessibility are
covered by component tests, CSS rules, lint, and production build.

## Independent review

The Trellis check agent found and fixed:

1. batched PydanticAI tool-call events must queue privately while the public
   lifecycle exposes only one open step;
2. terminal-tail browser events must fail the protocol closed;
3. the timeline disclosure must reset its auto-collapse state between turns.

Follow-up tests cover all three findings plus provider-context cleanup on early
consumer close.

## Final gates

- `python -m py_compile`: passed
- `scripts/export_web_openapi.py --check`: passed
- `task.py validate 08-22-agent-step-streaming`: passed (8 implement context
  entries, 7 check entries)
- `git diff --check`: passed
