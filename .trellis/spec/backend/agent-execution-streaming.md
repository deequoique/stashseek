# Agent Execution Streaming

## 1. Scope / Trigger

Use this contract whenever changing the primary Agent run loop, tool lifecycle
events, browser conversation SSE, transient Todo projection, answer streaming,
or the React execution timeline.

The product exposes a safe execution timeline, not provider reasoning. Real
tool boundaries may be visible; primary model prose, thinking, tool arguments,
raw tool returns, provider metadata, and exception text never cross the
Agent-to-browser boundary.

## 2. Signatures

Internal Agent events:

```python
AgentStreamEvent(
    type="step_started" | "step_completed" | "plan_updated",
    request_id: str,
    message_id: str,
    step_id: str | None,
    step_code: PublicStepCode | None,
    step_outcome: "completed" | "failed" | "skipped" | None,
    result_count: int | None,
    plan: tuple[AgentPlanItem, ...],
)
```

Public transport:

```http
POST /api/v1/conversations/{conversation_id}/messages/stream
Accept: text/event-stream
```

Every `ConversationStreamEvent` contains one request ID, the submitted message
ID, and a contiguous sequence starting at 1. FastAPI Pydantic models remain the
canonical contract; regenerate `web/src/api/openapi.json` and `schema.d.ts`
after changes.

## 3. Contracts

Primary streaming uses exactly one PydanticAI run:

```python
async with agent.run_stream_events(...) as events:
    async for event in events:
        # project function-tool boundaries and AgentRunResultEvent only
        ...
```

Keep the context manager so consumer close cancels and cleans up the background
provider run. Do not retry with `Agent.run()` after a streamed request has
started. A provider known not to support event streaming may select the existing
single non-streaming run before execution.

PydanticAI can emit all `FunctionToolCallEvent` values from one model response
before any `FunctionToolResultEvent`, even with sequential execution enabled.
Queue those private calls, expose only one public open step, then start the next
public step after the previous terminal event (and optional plan update).

Public step codes are a closed allow-list (`searching_library`,
`reading_context`, `checking_source`, `reviewing_library`, `checking_item`,
`handling_save`, `managing_library`, `updating_plan`, `working`). Unknown tools
map to `working`; raw tool names never appear in SSE or UI copy. Result counts
are optional integers from 0 through 10,000.

Successful `todo_write` may emit one complete `plan_updated` snapshot from the
validated `TurnTodoStore`: at most six items, closed status values, bounded
titles, and no URLs, server-owned IDs, payload markers, or control characters.
Plans and execution steps are transient and are not stored in
`ConversationTurn`.

The public lifecycle is:

```text
started -> activity(retrieving) ->
(step_started -> step_completed -> plan_updated?)* ->
activity(planning_answer) ->
(section_started -> text_delta* -> section_completed)* -> terminal
```

There is at most one public open step and one open answer section; they cannot
overlap. Terminal events are hard barriers.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| unknown/private tool | project `working`; omit raw name, args, return, IDs, and exception |
| batched tool calls in one model response | queue privately; serialize public start/result pairs |
| result without matching start or mismatched code | fail stream protocol closed |
| plan while step/section is open | fail stream protocol closed |
| duplicate step ID, sequence gap, wrong request/message | fail stream protocol closed |
| terminal-tail record | reject as `stream_protocol_error` |
| provider/timeout/cancel with open step | emit `step_completed(failed)` before terminal when transport remains writable |
| provider/timeout/cancel with open section | emit `section_aborted` before terminal when possible |
| browser disconnect / consumer close | close provider context; do not persist a partial turn |
| known provider lacks primary event stream | choose one non-streaming run before execution; keep activity/final-answer compatibility |
| answer provider lacks token streaming | retain the existing one-delta answer compatibility path; never resubmit the message |

## 5. Good / Base / Bad Cases

- Good: a search emits `step_started(searching_library)`, then
  `step_completed(completed, result_count=5)`, followed by Citation-first answer
  sections and one persisted terminal response.
- Base: a provider without primary event streaming produces no tool timeline
  but still performs exactly one compatibility run and returns the existing
  activity/answer lifecycle.
- Bad: forward `ToolCallPart.args`, `ToolReturnPart.content`, `ThinkingPart`,
  provider call IDs, raw query, Citation excerpts, or exception messages as a
  convenient timeline payload.

## 6. Tests Required

- Use a real fake streaming model and assert tool start/result events arrive
  before `completed`, with provider IDs and raw arguments absent.
- Cover multiple tool calls emitted in the same model response; assert only one
  public step is open and every started step terminates once.
- Close the Agent async generator while the next provider request is pending;
  assert the provider stream context exits.
- Cover success, failed, skipped, unknown tool, Todo replacement, invalid Todo,
  EOF, timeout, cancellation, sequence gap, duplicate step, mismatched code,
  terminal tail, and wrong correlation.
- Assert disconnect/aborted sections do not persist partial turns and replay of
  one message persists at most one turn.
- Scan SSE, DOM, and production logs for sentinels in questions, thinking,
  arguments, raw results, provider IDs, URLs, internal IDs, and exception text.
- Run generated API checks, frontend parser/UI tests, TypeScript, lint, and
  production build.

## 7. Wrong vs Correct

### Wrong

```python
if isinstance(event, FunctionToolCallEvent):
    yield {"tool": event.part.tool_name, "args": event.part.args}
```

This leaks model-controlled input and assumes call events alternate with result
events.

### Correct

```python
if isinstance(event, FunctionToolCallEvent):
    private_queue.append((event.part.tool_call_id, safe_code(event.part.tool_name)))
    if no_public_step_is_open():
        emit_step_started(server_step_id(), private_queue[0].safe_code)

if isinstance(event, FunctionToolResultEvent):
    emit_step_completed(current_server_step_id(), safe_server_observation())
    expose_next_private_call_if_any()
```

The browser sees a deterministic, allow-listed timeline while provider content
and authorization state remain private.
