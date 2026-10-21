# Design: Bounded Autonomy for the Knowledge Agent

## 1. Decision

Refactor the existing runtime around one primary tool-using Turn Agent while
retaining the current framework, channel contracts, Domain Services, durable
business state, and fail-closed safety boundaries.

The model decides whether an ordinary message needs private-knowledge tools.
The server does not require a complex structured final answer. The Agent emits
natural text with inline current-run Citation markers when grounded; the
application selects the validation path from actual tool/action traces.

For dependent multi-step requests the Agent may maintain a small turn-scoped
Todo. Failures are exposed through safe typed envelopes, and a deterministic
RecoveryPolicy grants a bounded set of legal next actions. The model may choose
among those actions; it never decides whether a retry is authorized.

No Inventory/Mutation/Retrieval executor classes, multi-Agent handoffs, or
state graph are introduced.

## 2. Target architecture

```text
ChannelService
  -> ContextBuilder
  -> TurnAgent (one primary reasoning/tool loop)
       <-> TurnTodoStore / todo_write
       <-> ToolsetPolicy
            -> RecoveryPolicy / RecoveryLedger
                 -> Atomic Tools
                      -> existing Domain Services
  -> AnswerValidator
  -> ConversationRepository
```

Component names describe responsibilities, not deployment processes. Existing
classes may retain compatibility names (`KnowledgeAgent`, conversation helper
functions) while internal responsibilities move into focused modules. The Todo
store and recovery ledger are ordinary in-memory objects owned by one `run()`;
they are not new services, executors, database records, or a state graph.

## 3. Boundaries by component

### 3.1 ChannelService

Unchanged authority boundary:

- resolve/register channel identity before any model or retrieval call;
- serialize one conversation in process and replay duplicate message IDs;
- create the trusted `AgentRequest`;
- persist only completed canonical results;
- keep slash commands and channel linking deterministic.

ChannelService must not classify natural-language intent or select tools.

### 3.2 ContextBuilder

Build a bounded, current-tenant `TurnContext` from existing state:

```python
@dataclass(frozen=True)
class TurnContext:
    history: tuple[dict, ...]
    recent_sources: tuple[ContextCitationRef, ...]
    recent_inventory: tuple[ContextItemRef, ...]
    explicit_references: tuple[NormalizedReference, ...]
    pending_save: PendingSaveSnapshot
    pending_delete: PendingDeleteSnapshot | None
```

Rules:

- Read only completed turns from the current thread and tenant.
- Project only bounded public fields already present in `sources` or
  `action_results`; omit provider payloads, internal dispatch/claim state and
  deleted/unavailable content.
- Preserve inventory ordering so the Agent can interpret “第二个”; item IDs are
  references, never authorization.
- The ContextBuilder does not create long-term memory or write a new state row.
  Current focus is derived from existing turn data, keeping rollback schema-free.
- Pending snapshots expose only the already-approved minimal state. Raw pending
  URLs/item IDs remain server owned as required by the existing contracts.

### 3.3 TurnAgent

The TurnAgent is the only model that chooses whether and how to use tools. It
receives the user message, normal bounded history and a concise trusted context
projection. It may:

- return a natural social/capability response with zero tools;
- ask a natural clarification with zero tools;
- call inventory/detail tools;
- call knowledge retrieval and expansion tools;
- call one terminal save/management action under existing rules;
- continue from a read-only inventory result into retrieval within the same
  turn.

It must not:

- choose or modify tenant identity;
- create scope from untrusted history when an explicit current-message scope
  exists;
- render authoritative mutation success or confirmation state;
- call arbitrary platform, filesystem, web, terminal, delegation or MCP tools;
- exceed runtime budgets or start background work outside an approved tool.

The initial implementation keeps PydanticAI and provider construction. A
provider spike must prove natural-text final output plus tool calls before the
old grounded Composer path is removed or bypassed.

### 3.4 TurnTodoStore and `todo_write`

Todo is optional model working memory for one dependent multi-step request. It
is deliberately not inferred for every turn and is not part of business state:

```python
TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]

@dataclass(frozen=True)
class TurnTodoItem:
    id: str
    title: str
    status: TodoStatus

@dataclass(frozen=True)
class TurnTodoSnapshot:
    items: tuple[TurnTodoItem, ...]
```

`todo_write` accepts a complete replacement snapshot so that the server can
validate every transition atomically and returns the normalized snapshot. Its
input contains only short human-readable step descriptions and stable
turn-local IDs; it must reject URLs, external or internal identifiers, raw tool
arguments/results, evidence excerpts, and oversized text.

Invariants:

- zero to six items; unique IDs; no empty title;
- at most one `in_progress` item;
- only the four states above; no `retrying` or `recovering` state;
- normal non-action completion has no `pending` or `in_progress` items;
- `blocked` is allowed only when the visible answer asks for missing context or
  truthfully reports an unavailable remainder;
- terminal `ActionOutcome` may pre-empt Todo finalization and always wins;
- every call consumes the existing global tool/request budget but no retrieval
  budget or separate hidden quota.

The store lives only inside one TurnAgent run. It is not placed in
`ConversationTurn`, model history, PostgreSQL, production diagnostics, or a
future-turn `TurnContext`. Todo state never proves that a tool ran, a Citation
exists, or a mutation succeeded; trusted tool/action traces remain authoritative.

Prompt guidance should make use selective:

| Request shape | Todo expectation |
| --- | --- |
| greeting, thanks, capability question | do not use |
| one clarification or one atomic tool call | normally do not use |
| list then resolve an ordinal then retrieve then answer | use |
| terminal mutation request | do not use unless prior dependent read steps are truly required |

### 3.5 ToolsetPolicy

ToolsetPolicy is a server-owned registration/prepare policy, not an intent
router. It never decides what the user means. It narrows the possible actions
using trusted facts:

- feature-disabled tools are absent;
- matching confirm/cancel/clarify tools are exposed only for an active pending
  action of that kind;
- explicit URL content scope hides unrelated inventory/mutation tools and
  continues to constrain every evidence read;
- read-only inventory observations register a bounded set of item references
  that later same-turn scoped retrieval may use;
- destructive and submission tools retain their input-equality, batch, TTL,
  idempotency and terminal-outcome rules;
- hard PydanticAI request/tool/output limits and local 5/2/3 retrieval limits
  remain independent of model judgment.

The first iteration does not add `select_capabilities`, free-form tool search,
or a second model router. If the behavioral corpus shows continued tool-choice
confusion, lazy toolset selection becomes a separately reviewed follow-up.

### 3.6 ErrorEnvelope, RecoveryPolicy, and RecoveryLedger

Domain/tool adapters map expected failures to a small safe envelope. Raw
exceptions remain server-side and pass through the existing redacted diagnostic
mapping:

```python
RecoveryCategory = Literal[
    "transient_read",
    "read_unavailable",
    "missing_context",
    "policy_or_security",
    "side_effect_indeterminate",
    "provider_failure",
    "answer_validation",
]

@dataclass(frozen=True)
class ErrorEnvelope:
    category: RecoveryCategory
    code: str                 # stable allow-listed public code
    operation: Literal["read", "mutation", "provider", "answer"]
    safe_message: str
    partial_evidence: bool

@dataclass(frozen=True)
class RecoveryGrant:
    allowed: tuple[RecoveryAction, ...]
    remaining_actions: int
```

An envelope never contains exception text, response body, URL, query, tenant,
thread, item/pending/claim ID, SQL, stack trace, tool payload, or evidence. The
model sees the envelope plus `RecoveryGrant`; it cannot author either. For an
exact read retry the ledger binds permission to a server-side fingerprint of
the tool name and normalized arguments, while diagnostics record only category,
outcome, and counts—not the fingerprint inputs.

The policy matrix is deterministic:

| Condition | Permitted handling |
| --- | --- |
| transient read failure | retry the exact read once, use existing evidence, return a partial result, or report unavailable |
| permanent/unclassified read failure | use existing evidence, partial result, or stable unavailable; no same-call retry |
| empty search result | normal observation, not `ErrorEnvelope`; reformulate within search and recovery budgets or stop |
| missing trusted context | ask the user; mark the affected Todo item `blocked` |
| tenant/scope/policy/deleted/non-ready failure | stop with canonical safe result; no alternate-tool workaround |
| mutation or side effect in progress/unknown | canonical action result only; no autonomous retry |
| provider/model failure | no Agent retry; evidence fallback if available, otherwise stable failure |
| answer/Citation validation failure | one tool-free repair against the same evidence, then evidence fallback/stable failure |

Hard ceilings:

- same read tool with the same normalized arguments: at most one recovery retry;
- all recovery actions in one turn: at most two;
- answer repair: at most one and it also consumes the whole-turn recovery limit;
- mutations, confirmations, and provider/model calls after provider failure:
  zero autonomous retries;
- every recovery consumes the pre-existing request, raw-tool, retrieval-stage,
  output, and wall-clock budgets. Recovery never creates fresh budget.

Search reformulation after an empty observation counts as a recovery action for
the whole-turn ceiling while also consuming the normal search budget. A user-
requested domain operation named “retry” (for example retrying failed ingestion)
is a new validated terminal action, not an Agent recovery retry.

`RecoveryLedger` is turn-scoped and tracks safe category counts, exact-read
retry fingerprints, answer-repair count, and total recovery actions. It is not
persisted or added to model history. `RecoveryPolicy` is the sole writer of
grants. The Agent chooses only among granted actions; if no action is granted it
must finish with the provided safe outcome.

Todo and recovery remain separate. A Todo status change cannot grant a retry or
tool, and a recovery does not add Todo states. On recoverable failure the current
step can remain `in_progress`; on exhaustion it becomes `blocked`, while already
successful steps remain `completed`. Final output is checked against the trusted
trace, never against the Todo's claim of success.

### 3.7 Domain Services

Keep the existing service ownership:

```text
KnowledgeServices                 retrieval and exact scope
KnowledgeItemManagementService   tenant-bound inventory and item mutation
IngestSubmissionService          URL validation and durable dispatch
PendingConfirmationService       durable confirmation and delete effects
```

Tools remain thin adapters. No service accepts model-authored tenant/thread or
claim identifiers. No retrieval SQL or ingestion state-machine rewrite is
part of this task.

### 3.8 AnswerValidator

The validator selects behavior from trusted execution facts, not a model-
authored `mode`:

| Actual run trace | Required final behavior |
| --- | --- |
| terminal `ActionOutcome` | discard model prose; return canonical action answer |
| successful knowledge search | require and validate current-run `[S…]` markers |
| read-only inventory/detail only | allow bounded tool-supported prose; no Citation/source claims |
| no tools | allow bounded conversation or clarification; no markers/source block |
| explicit URL content question with no successful in-scope search | fail/repair; never accept no-tool content answer |
| knowledge tool limit/timeout after evidence | validate/repair against captured evidence or use evidence fallback |
| retrieval/embedding service failure | preserve distinct fail-closed service error |
| read steps partly succeeded, later read exhausted recovery | return only supported partial result and name the unavailable remainder |
| provider failed with no evidence | stable provider failure; do not re-run the TurnAgent |

Grounded natural text validation:

1. Parse only exact `[S<positive integer>]` markers.
2. Require at least one marker after a successful search.
3. Require every marker to exist in the current-run Citation cache.
4. Reapply exact current-message reference scope to every selected Citation.
5. Enforce the current item/source count limit.
6. Reject model-authored URLs and server-style source blocks.
7. Render the canonical source block from selected trusted Citations.
8. On invalid output, perform at most one provider-compatible repair against
   the same evidence allow-list if RecoveryPolicy grants it; never retrieve
   during answer repair.
9. If repair fails, use the existing deterministic evidence fallback.

If Todo was used, a pre-validation finalization check also requires zero
`pending`/`in_progress` items on normal completion. A partial or clarification
answer may retain `blocked` items only when its visible wording matches the
trusted failure/context trace. The validator does not trust `completed` as proof
of a read or side effect, and never lets Todo text enter the user answer.

Inline markers provide the same mechanical Citation-origin guarantee as the
current `AnswerDraft.citation_ids`; neither representation proves semantic
entailment. Semantic support is evaluated through the behavior corpus rather
than assumed from JSON shape.

Read-only inventory observations must be separated from terminal mutation
outcomes. A new internal observation cache may record bounded item IDs/URLs
returned during the current run. It does not become public authorization or a
new database state machine.

### 3.9 ConversationRepository

Retain the existing thread/turn tables and duplicate replay behavior.

- Persist only the visible final answer and current-run canonical sources.
- Preserve bounded read-only `action_results` needed to reconstruct inventory
  ordering and same-thread focus.
- Do not persist raw tool calls/results, invalid drafts, prompts, provider
  payloads, Todo snapshots, RecoveryLedger data, or pending targets as
  conversation history.
- Continue excluding synthetic MCP management markers from the model history
  cap.
- No database migration is planned. If implementation proves existing fields
  insufficient, stop at the planning gate and request a separate schema design
  instead of smuggling state into unrelated JSON.

## 4. Run sequences

### 4.1 Social conversation

```text
user: “谢谢”
  -> ContextBuilder
  -> TurnAgent returns natural text, no tools
  -> no Todo created
  -> AnswerValidator confirms no markers/source block
  -> persist one completed turn
```

No embedding, retrieval, Composer or action service is constructed.

### 4.2 Ordinary knowledge question

```text
user question
  -> TurnAgent decides knowledge is needed
  -> search_segments -> optional expansion
  -> natural answer with [S123] [S456]
  -> AnswerValidator checks current-run allow-list and scope
  -> server appends canonical sources
```

If the model incorrectly chooses a no-tool reply for an ordinary non-URL
knowledge question, it is a behavioral miss recorded by the evaluation corpus.
It cannot access new tenant data without a tool. Explicit URL content questions
retain a deterministic grounding obligation and cannot take this no-tool exit.

### 4.3 Clarification

```text
user: “总结第二个”
  -> no trusted prior inventory/focus candidate exists
  -> TurnAgent asks which list/item the user means
  -> no tools, no Citation, no search_required
```

### 4.4 Inventory then summarize

```text
user: “列出我的收藏，然后总结第二个”
  -> todo_write: [list, resolve second, retrieve, answer]
  -> list_saved_items returns bounded rows as a nonterminal observation
  -> mark list completed; observation cache registers ordered references
  -> TurnAgent selects the second returned item; mark resolve completed
  -> scoped search_segments -> optional neighbors; mark retrieve completed
  -> natural cited answer; mark answer completed
  -> validator proves every Citation belongs to the selected tenant item
```

Mutation tools remain terminal, so “删除第二个然后总结第三个” returns the
canonical confirmation request and does not perform an additional task in this
iteration.

### 4.5 Inventory then summarize with one transient failure

```text
user: “列出我的收藏，然后总结第二个”
  -> create the same four-step Todo
  -> list_saved_items succeeds; list step completed
  -> selected item's search_segments returns transient_read ErrorEnvelope
  -> RecoveryPolicy grants retry_same_read (remaining recovery actions: 1)
  -> TurnAgent chooses retry; ledger binds exact tool/arguments
  -> retry succeeds; retrieve and answer steps completed
  -> validate current-run Citation markers and persist only final answer/sources
```

If the retry fails, no second same-call retry is granted. With a trusted list but
no retrieval evidence, the Agent may return the list and state that the summary
is unavailable; the list step remains `completed`, retrieve/answer become
`blocked`, and no Citation is fabricated. If useful evidence already exists,
the policy may instead permit the deterministic evidence fallback.

### 4.6 Action and pending confirmation

```text
explicit save/delete/update/restore/retry
  -> existing action tool/service
  -> terminal ActionOutcome
  -> model prose discarded
  -> canonical response and durable result persisted/replayed
```

The model may decide to invoke the action tool, but it never decides whether the
effect succeeded or whether confirmation was valid.

## 5. Provider and budget compatibility

- Preserve `parallel_tool_calls=False` and local sequential execution.
- Capability/read-only observations count toward the global hard tool-call
  limit. `todo_write` also counts as a tool call; only actual retrieval service
  calls consume 5/2/3 retrieval budgets.
- Recovery actions use the same ledger-backed two-action ceiling and all
  existing budgets. Exhausted request/tool/time budget always overrides a
  RecoveryGrant.
- A social/no-tool turn uses one TurnAgent run and no Composer.
- Grounded turns initially retain a rollback-compatible answer repair/fallback
  path. The provider spike determines whether repair stays in the same run or
  uses one tool-free call with the captured evidence.
- Never raise limits as the primary solution. Record request/tool counts,
  outcome class and phase with existing privacy-safe diagnostics.

## 6. Rollout and rollback

Add a validated default-off flag such as:

```dotenv
AGENT_BOUNDED_AUTONOMY_ENABLED=false
```

Rollout order:

1. deterministic tests and local FunctionModel traces;
2. CLI/MCP read-only A/B with the same tenant and behavior corpus;
3. explicit development-channel smoke;
4. enable for a limited channel deployment;
5. enable by default only after user review of behavioral and safety results.

When disabled, use the current planner/Composer/search-required path. The new
path writes only existing compatible turn fields, so rollback requires no data
migration. Do not keep two divergent Domain Service implementations.

## 7. Risks and mitigations

### Missed retrieval

The model may answer an ordinary knowledge question without tools. Measure this
explicitly. Improve instructions/tool descriptions and context before adding a
second router; retain deterministic grounding for explicit URL questions.

### Unnecessary retrieval

The model may still search for greetings. Measure zero-tool correctness and
keep the system prompt concise. Do not solve by a growing regex intent table.

### Citation formatting drift

Use a strict marker grammar, current-run allow-list, one bounded repair and
deterministic fallback. Server owns URLs and source rendering.

### Tool overload

State-based ToolsetPolicy removes impossible pending/feature/scope tools. Lazy
capability activation is out of scope until traces demonstrate the need.

### Read-only/action regression

Separate observation from terminal outcome narrowly. Mutations retain the old
terminal path and full pending/action regression suite.

### Provider incompatibility

Run a bounded spike against deterministic models and the configured real
provider before removing the old grounded path. Flag-off rollback remains
available.

### Todo overuse or false progress

Keep Todo optional, capped at six entries, absent from simple/single-tool
fixtures, and non-authoritative. Finalization checks statuses, while actual
success continues to come only from tool/action traces.

### Recovery loops or unsafe retries

Use typed categories, server-issued grants, exact-call fingerprints, a
turn-scoped ledger, and hard 1/2/1 retry ceilings. Mutations and provider
failures receive no autonomous retry grant; policy/security failures cannot be
converted to another tool path.

## 8. Validation strategy

The behavior corpus is a first-class artifact. It combines user-supplied real
texts with synthetic boundary cases and records expected tool class, whether
grounding is required, expected Citation/action properties, and acceptable
response intent without storing private production content. Each case also
labels `should_use_todo`, `should_not_use_todo`, expected recoveries, and the
maximum permitted retry/repair counts.

Measure at least:

- correct zero-tool conversation rate;
- missed-search and unnecessary-search rate;
- correct clarification rate;
- tool selection/order for read-only composition;
- correct Todo use/non-use and final status convergence;
- expected recovery choice, partial-success handling, and retry-ceiling compliance;
- Citation-origin and exact-scope correctness;
- mutation/confirmation outcome parity;
- provider requests, tool calls, latency and token use;
- exactly-one visible reply and restart/duplicate persistence behavior.

Automated validators remain the release gate for authorization and destructive
effects. Naturalness is reviewed with the real-model corpus rather than inferred
from deterministic fake-model tests.
