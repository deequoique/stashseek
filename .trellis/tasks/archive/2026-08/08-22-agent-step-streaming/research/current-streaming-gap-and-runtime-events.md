# Current streaming gap and runtime event evidence

## Current request path

The browser already consumes a strict SSE lifecycle from
`POST /api/v1/conversations/{conversation_id}/messages/stream`. The route
projects `ChannelService.handle_stream()`, and `KnowledgeAgent.stream()` emits
safe answer-section lifecycle events.

The citation-first answer pipeline is genuinely incremental: after a validated
section plan, each grounded section produces provider text deltas and the
browser renders those deltas immediately. This part does not explain the long
silent interval reported by the user.

The gap is the primary Agent run. `KnowledgeAgent.stream()` awaits
`_run_primary_agent()` before it emits anything more specific than the initial
`retrieving` activity. `_run_primary_agent()` calls `Agent.run()`, so all model
turns and all retrieval/action tools complete behind one coarse status. The UI
then replaces that same status string until answer sections begin. It has no
execution-step collection or timeline.

## Local runtime capability

The installed runtime is PydanticAI 2.15.0. Its local API exposes
`Agent.run_stream_events()` as an async context manager. The documented local
implementation guarantees:

- the background run starts when the event iterator is first consumed;
- early consumer exit deterministically cleans up the background task;
- the stream ends with `AgentRunResultEvent`, carrying the same final run result;
- function tools emit `FunctionToolCallEvent` followed by
  `FunctionToolResultEvent`;
- provider text, thinking parts, tool-call parts and their deltas also appear in
  the same internal stream and therefore must be filtered rather than projected.

A local no-network spike with `TestModel` observed this order:

```text
PartStart(tool call)
PartEnd(tool call)
FunctionToolCallEvent
FunctionToolResultEvent
PartStart(final text)
FinalResultEvent
PartDelta(final text)*
PartEnd(final text)
AgentRunResultEvent
```

This proves the primary run can expose real tool boundaries while retaining one
authoritative result. It does not make provider thinking or primary-agent prose
safe to publish.

## Existing safe server state

`AgentDeps.tool_event()` and `ToolPolicy` already record server-owned tool name,
call index, outcome and optional result count without recording arguments or
tool payloads. `TurnTodoStore` owns a validated bounded Todo snapshot. These are
better inputs for a public projection than the raw PydanticAI result event.

The primary runtime enforces sequential tool execution, so a single open public
tool step can be paired deterministically with its result. Public step IDs can
be server-generated (`step-1`, `step-2`, …); provider tool-call IDs need not
cross the trust boundary.

## Conclusion

The next implementation should replace only the streaming primary-run seam with
`run_stream_events()`, ignore all raw model/provider content, and project
allow-listed tool lifecycle plus validated Todo snapshots into a browser-safe
execution timeline. The non-streaming `run()` path and the existing answer
section stream remain compatible references.
