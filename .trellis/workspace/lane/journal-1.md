# Journal - lane (Part 1)

> AI development session journal
> Started: 2026-08-04

---



## Session 1: 继续实现 video-text-kb P0

**Date**: 2026-08-04
**Task**: 继续实现 video-text-kb P0

### Summary

完成 P0 步骤1-6本地代码：YouTube android_vr 摄入、空成功守卫、五级切分、嵌入、双路检索与CLI；19项测试、编译、依赖、Docker迁移和索引验证通过。任务保持 in_progress，等待 OPENAI_API_KEY 与20视频真实人工验收；当前目录无Git元数据，未提交。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

(No commits - planning session)

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: 日志初步搭建

**Date**: 2026-08-07
**Task**: 日志初步搭建
**Branch**: `main`

### Summary

完成 LangBot bridge 与 Notebook Agent 的结构化诊断日志：Notebook Agent 双写 stdout/每日文件，bridge 仅写 plugin stderr；使用 trace/request ID 联查，生产默认脱敏，本地 development+显式开关可记录受限检索详情。修复日志初始化幂等、权限失败关闭流和保留清理 fallback，Terra 定向与全量回归通过，并补充部署与排障文档。

### Git Commits

| Hash | Message |
|------|---------|
| `c55688c` | (see git log) |
| `128f15f` | (see git log) |

### Status

[OK] **Completed**


## Session 3: Agent retrieval convergence and provider-compatible answers

**Date**: 2026-08-08
**Task**: Agent retrieval convergence and provider-compatible answers
**Branch**: `main`

### Summary

Implemented provider-independent retrieval convergence, diversified evidence selection, independent answer composition, development HTTP diagnostics, and PromptedOutput compatibility for DeepSeek Thinking mode.

### Main Changes

- Enforced one backend retrieval per model step with bounded 5/2/3 budgets and typed skipped results.
- Added Top-5 source diversity, independent answer composition, citation validation, canonical history, and deterministic evidence fallback.
- Captured full provider HTTP errors in development, diagnosed Thinking mode rejecting tool_choice=required, and switched composer to PromptedOutput.

### Git Commits

| Hash | Message |
|------|---------|
| `27140c8` | (see git log) |
| `3c35631` | (see git log) |
| `ced5cac` | (see git log) |

### Testing

- [OK] Earlier directed diagnostics/runtime validation passed 46 tests; PromptedOutput switch was not run by Codex at user request, and the user confirmed the task complete.

### Status

[OK] **Completed**


## Session 4: Composer 预算与自动上下文压缩

**Date**: 2026-08-08
**Task**: Composer 预算与自动上下文压缩
**Branch**: `main`

### Summary

为回答 Composer 设置 1000-token provider 上限并关闭 DeepSeek thinking；在输出超限或长度截断时按可信证据自动压缩重试，补齐安全诊断、规范与回归测试。

### Git Commits

| Hash | Message |
|------|---------|
| `fa1dff9` | (see git log) |
| `6e51bb2` | (see git log) |

### Status

[OK] **Completed**


## Session 5: Deploy competition environment on Vercel and Neon

**Date**: 2026-08-08
**Task**: Deploy competition environment on Vercel and Neon
**Branch**: `codex/vercel-neon-main-sync`

### Summary

Connected Vercel Production to GitHub main, upgraded Neon to d4e5f6a7b8c9, documented organization-scoped team access and local DATABASE_URL setup, added migration-head drift checks, fixed static source disclosure, and verified public health and 404 routes.

### Git Commits

| Hash | Message |
|------|---------|
| `3dbc919` | (see git log) |
| `0e13425` | (see git log) |
| `72181b3` | (see git log) |
| `3de5d7a` | (see git log) |
| `c1599b4` | (see git log) |

### Status

[OK] **Completed**


## Session 6: Tenant-scoped MCP server

**Date**: 2026-08-08
**Task**: Tenant-scoped MCP server
**Branch**: `codex/mcp-server-optional-langbot`

### Summary

Added the official MCP v2 stdio and Streamable HTTP adapter, tenant-bound hash-only grants, scope-gated tools, fail-closed mutation readiness, MiXer path-token compatibility, CLI lifecycle operations, durable delete safety, documentation, code-specs, and protocol-level tests.

### Git Commits

| Hash | Message |
|------|---------|
| `c11c9d1` | (see git log) |

### Status

[OK] **Completed**


## Session 7: Deployment environment configuration guide

**Date**: 2026-08-08
**Task**: Deployment environment configuration guide
**Branch**: `main`

### Summary

Reorganized the root dotenv template and added scenario-first environment profiles for local read-only MCP, full mutation MCP, Streamable HTTP/MiXer, and optional LangBot, with bilingual quick-start and deployment guidance.

### Main Changes

- Added a canonical environment configuration guide and complete variable matrix.
- Updated dotenv grouping, stdio grant startup, worker queues, migration head, and bilingual entry points.

### Git Commits

| Hash | Message |
|------|---------|
| `18707d3` | (see git log) |

### Testing

- [OK] 60 focused MCP, diagnostics, provider, and deployment tests passed.
- [OK] Validated 52 dotenv keys, 54 config consumers, local Markdown links, and git diff hygiene.

### Status

[OK] **Completed**


## Session 8: Fix exact video reference and session routing

**Date**: 2026-08-09
**Task**: Fix exact video reference and session routing
**Branch**: `main`

### Summary

Enforced deterministic bare-URL save confirmation and exact platform/video retrieval scopes so stale session history cannot route or cite the wrong saved video.

### Main Changes

- Added server-owned routing invariants for bare supported URL batches and explicit URL content questions.
- Applied fail-closed exact video scope across lexical/vector retrieval, hydration, neighbors, details, timestamps, and citations.
- Restricted management and save tools using only the current user message while preserving legitimate management follow-ups.

### Git Commits

| Hash | Message |
|------|---------|
| `105d0db` | (see git log) |

### Testing

- [OK] 26 passed: ingest submission and agent actions.
- [OK] 45 passed: knowledge services, agent runtime, and exact video routing.
- [OK] 35 passed, 16 skipped: multiuser integration and item management; compileall and Trellis validation passed.
- [OK] Broader suite reached 244 passed, 34 skipped; remaining failures were environment-bound PostgreSQL/socket/composition checks.

### Status

[OK] **Completed**


## Session 9: Add durable ingestion completion queue

**Date**: 2026-08-09
**Task**: Add durable ingestion completion queue
**Branch**: `main`

### Summary

Added transactional completion outbox, durable completion queue, bounded repair sweep, Redis persistence contract, migration, tests, docs, and backend spec.

### Git Commits

| Hash | Message |
|------|---------|
| `998baea` | (see git log) |

### Status

[OK] **Completed**


## Session 10: Ingest completion source-channel notifications

**Date**: 2026-08-09
**Task**: Ingest completion source-channel notifications
**Branch**: `dev`

### Summary

Added a 10-second Celery Beat poller over durable PostgreSQL completion events, idempotent delivery/retry ledger, trusted source-thread propagation, LangBot outbound notifications, migrations, observability, deployment docs, and unit/PostgreSQL coverage. Archived the completed Trellis task; known bounded-autonomy regressions remain outside this task.

### Git Commits

| Hash | Message |
|------|---------|
| `42873cc` | (see git log) |

### Status

[OK] **Completed**


## Session 11: Real-model natural language evaluation

**Date**: 2026-08-09
**Task**: Real-model natural language evaluation
**Branch**: `dev`

### Summary

Added a 22-case real-model MCP/context evaluator, verified the complete stack with a 6/6 smoke run, hardened teardown/report privacy, and archived the Trellis task.

### Main Changes

- Added opt-in real-model natural-language catalog, runner, fixtures, trace correlation, scoring, and sanitized reports.
- Verified PostgreSQL/pgvector, Redis/Celery, MinIO, ingestion, embedding, MCP and context persistence through a dedicated retained-data evaluation user.
- Hardened stdio diagnostic capture, cancellation, teardown deadlines, grant revocation, production refusal, and safety-critical scoring.

### Git Commits

| Hash | Message |
|------|---------|
| `877459b` | (see git log) |
| `8e526b7` | (see git log) |

### Testing

- [OK] Catalog 1.0.0: 22 cases valid.
- [OK] 49 evaluator/diagnostics tests and 4 readiness tests passed.
- [OK] Paid full-stack smoke: 6 pass / 0 fail / 0 skip.

### Status

[OK] **Completed**

### Next Steps

- Run the full 22-case paid evaluation only with explicit operator authorization.


## Session 12: Simplify deployment lifecycle

**Date**: 2026-08-09
**Task**: Simplify deployment lifecycle
**Branch**: `dev`

### Summary

Added a profile-aware one-command launcher with minimal runtime configuration, supervised worker and Beat lifecycle, readiness checks, secure shutdown, tests, and deployment documentation.

### Main Changes

- Added read/full/langbot initialization and lifecycle commands
- Documented minimal environment configuration and deployment safety

### Git Commits

| Hash | Message |
|------|---------|
| `872c7b7` | (see git log) |
| `c0dd2d2` | (see git log) |

### Testing

- [OK] 35 deployment CLI tests and 102 related regression tests passed

### Status

[OK] **Completed**


## Session 13: Include LangBot gateway in full profile

**Date**: 2026-08-10
**Task**: Include LangBot gateway in full profile
**Branch**: `codex/full-includes-langbot`

### Summary

Redefined full as worker, Beat, MCP, and Notebook Agent gateway with dual-listener lifecycle checks and aligned operator docs.

### Main Changes

- Added gateway startup, secret handling, dual-listener readiness/status/fingerprint coverage, and fail-fast port collision validation to full.
- Updated English/Chinese deployment documentation and the stable lifecycle specification.

### Git Commits

| Hash | Message |
|------|---------|
| `4ccfae8c57c741ba76ffdfe124e1a02c62ed38db` | (see git log) |
| `677f6cf` | (see git log) |

### Testing

- [OK] Affected regression suite: 105 passed.
- [OK] Independent broader regression: 111 passed, 1 known MCP stdio cold-start test deselected; the timeout reproduces on unmodified dev.
- [OK] Python compile, shell syntax, diff check, and Trellis validation passed.

### Status

[OK] **Completed**

### Next Steps

- Push the reviewed commits to origin/dev while leaving main untouched.


## Session 14: Web email auth contract and full-browser readiness

**Date**: 2026-08-10
**Task**: Web email auth contract and full-browser readiness
**Branch**: `codex/web-email-auth-contract-fix`

### Summary

Unified browser email authentication, canonical FastAPI composition, generated API contracts, frontend login/session behavior, and archived the Trellis task after full automated validation.

### Main Changes

- Unified production browser API composition and email auth/session/logout contracts.
- Aligned React Query session rotation, routes, generated OpenAPI types, CI, and regression coverage.
- Recorded browser runtime conventions in Trellis specs and archived the completed task.

### Git Commits

| Hash | Message |
|------|---------|
| `b883b27` | (see git log) |
| `1ae3d9f` | (see git log) |
| `424e7f5` | (see git log) |

### Testing

- [OK] Python full suite: 542 passed, 74 skipped.
- [OK] Frontend Vitest 79/79, API contract check, lint, typecheck, and production build passed.
- [OK] Mobile browser login/logout smoke passed without console errors or horizontal overflow.

### Status

[OK] **Completed**

### Next Steps

- Merge codex/web-email-auth-contract-fix into main and run real dev database ingestion end to end.


## Session 15: Fix YouTube worker trusted CA initialization

**Date**: 2026-08-10
**Task**: Fix YouTube worker trusted CA initialization
**Branch**: `codex/youtube-subtitle-ca-fix`

### Summary

Initialized and exported the verified CA bundle before worker-owned YouTube metadata and subtitle subprocesses, added ordering/fail-closed/inheritance tests, updated backend contracts, and verified a real dev ingest reached ready with valid transcript segments and embeddings.

### Git Commits

| Hash | Message |
|------|---------|
| `544b809` | (see git log) |
| `2be7c71` | (see git log) |

### Status

[OK] **Completed**


## Session 16: Modularize bounded Agent runtime

**Date**: 2026-08-17
**Task**: Modularize bounded Agent runtime
**Branch**: `dev`

### Summary

Split runtime.py into state, builder, tool policy/registrations, answer pipeline, and orchestration modules; removed legacy/default runtime and three feature flags; made save and item management always available; updated specs, docs, evaluator, and tests.

### Git Commits

| Hash | Message |
|------|---------|
| `38e0804` | (see git log) |

### Status

[OK] **Completed**


## Session 17: 逐步可见的 Agent 流式输出

**Date**: 2026-08-22
**Task**: 逐步可见的 Agent 流式输出
**Branch**: `dev`

### Summary

实现安全的 Agent 执行步骤流、前端临时时间线、严格生命周期校验与完整测试文档；工具参数、结果、推理及供应商标识保持私有。

### Git Commits

| Hash | Message |
|------|---------|
| `fa3423e` | (see git log) |
| `77bc57a` | (see git log) |

### Status

[OK] **Completed**


## Session 18: Fix conversation continuity and list latency

**Date**: 2026-08-23
**Task**: Fix conversation continuity and list latency
**Branch**: `dev`

### Summary

Preserved terminal streamed answers through transcript convergence, made new conversations immediately usable, replaced conversation-list N+1 queries, and added secure Redis-backed opaque Session caching with request-scoped auth reuse.

### Main Changes

- Added generation-safe bounded Redis Session projection caching while keeping PostgreSQL authoritative.
- Replaced per-thread latest-turn reads with one paged set-based projection.
- Kept terminal answers visible until durable transcript handoff and decoupled new-conversation readiness from sidebar refresh.

### Git Commits

| Hash | Message |
|------|---------|
| `569ff9f` | (see git log) |
| `11cf7ae` | (see git log) |
| `58a70d8` | (see git log) |

### Testing

- [OK] 54 focused backend tests and 140 frontend tests passed; TypeScript, ESLint, Vite, OpenAPI, Alembic and Trellis checks passed.
- [OK] Local Mailpit HTTPS timing improved from about 3 seconds to a warm median of about 1.03 seconds; SQL execution measured 0.073 ms.

### Status

[OK] **Completed**

### Next Steps

- No push performed; monitor remote Neon latency and only consider connection-topology changes with separate approval.


## Session 19: Rewrite product README and user documentation

**Date**: 2026-08-23
**Task**: Rewrite product README and user documentation
**Branch**: `dev`

### Summary

Reframed Notebook Agent around its product value, added public product screenshots and a four-step user journey, reorganized user documentation with Diataxis, and preserved production deployment runbooks.

### Main Changes

- Rewrote English and Chinese README files as product landing pages with live demo links and public screenshots.
- Reorganized docs into tutorials, how-to guides, reference, explanation, and production operations.
- Restored the Debian/Nginx atomic frontend deployment and rollback contract under the new operations path.

### Git Commits

| Hash | Message |
|------|---------|
| `0c169ae` | (see git log) |
| `5d58134` | (see git log) |

### Testing

- [OK] 138 deployment, MCP, notification, and split-frontend tests passed.
- [OK] 145 Web auth, source connector, and browser-companion tests passed.
- [OK] All local links and image targets in 32 Markdown files resolved; git diff checks passed.

### Status

[OK] **Completed**


## Session 20: Add community and GitHub entry points

**Date**: 2026-08-30
**Task**: Add community and GitHub entry points
**Branch**: `dev`

### Summary

Added a dismissible Chinese community announcement, first-level Tencent QQ and GitHub actions, a public GitHub contribution link, responsive styling, persistence fallback, tests, and frontend state guidance.

### Git Commits

| Hash | Message |
|------|---------|
| `0deeb7e` | (see git log) |

### Status

[OK] **Completed**


## Session 21: 会议问答质量：RQ1 前置修复与检索研究

**Date**: 2026-10-06
**Task**: 会议问答质量：RQ1 前置修复与检索研究
**Branch**: `dev`

### Summary

诊断 QMSum specific-30 低分根因，修复分块/回答阶段 fail-closed/embedding 重试三处生产缺陷，完成 RQ1 检索研究；端到端成功率 18/30→30/30。代码未提交（工作区混有约 230 个无关 WIP 改动，按用户决定延后提交）。

### Main Changes

- 分块：语义优先 + 下限 80/上限 200 词 + 窗口内最深语义落差切分 + 15% 整句重叠；中位块 12→155 词；回答器摘录上限 360→1200
- 回答阶段：流式空白片段不再中止回答；重复/超 8 个引用由服务器按段轮转归一化（types.py/response.py 不变量相应放宽）
- embedding：瞬时错误分类 + 每批最多 3 次有界重试；超长转录 embedding 预算不足时退化为停顿/标点切分
- 评测：evals/meeting_gold 新增 rq1 与 agent-smoke 子命令（生产链路入库、9 种检索×多样性×两种范围、精确上限、McNemar/Wilson/bootstrap）
- 规格：新增 ingestion-chunking-embedding.md；更新 agent-retrieval-convergence.md、browser-companion-capture.md

### Git Commits

(No commits - planning session)

### Testing

- [OK] 相关测试 145 通过（chunker/tasks/embed/agent_runtime/streaming/normalization）；meeting_gold 129 通过；全量失败集与改动前一致（32 个既有失败）
- [OK] 生产链路端到端重跑：回答 30/30（修复前 18/30），严格块级 Recall@5 0.147→0.397，引用含金标准 0.56→0.89
- [OK] RQ1：上限 Recall@5 0.953；基线 A 0.381 / B 0.139；B 内最佳 rrf-OR 关多样性 +0.111 [0.019,0.216]

### Status

[OK] **Completed**

### Next Steps

- 提交本次代码（需拆分与无关 WIP 混在同一文件中的改动）
- 修复 HNSW 租户过滤截断（全库向量检索 25/30 拿不到 10 个候选）
- 按 RQ1 结论改检索：OR 查询 + RRF + 全库关闭多样性
- 检索 Agent 预算问题；模糊主题问题的查询改写；清理测试库评测条目


## Session 22: Release meeting-QA fixes to production

**Date**: 2026-10-07
**Task**: Release meeting-QA fixes to production
**Branch**: `release/meeting-qa-fixes`

### Summary

Ported the completed meeting-QA fixes from dev onto origin/main in an isolated worktree (no WIP), merged them as PR #25 and deployed 74182624 to production. Post-deploy probe passed.

### Main Changes

- Chunking: semantic-first windows with an 80/130 lower bound, a 200/330 upper bound, the deepest-dip cut, ~15% cue overlap and the embedding budget guard
- Answer: whitespace stream deltas allowed; citations normalized by section round-robin (cap 8)
- Embed: classified per-batch retry for transient errors
- Retrieval: filter the tenant first, then rank exactly by vector; migration d04fdae36884 drops ix_segment_embedding_hnsw
- Agent: batched retrievals run within the 5/2/3 budgets; no discarded post-search text; stage_usage diagnostics; AGENT_OUTPUT_TOKEN_LIMIT=3000

### Git Commits

| Hash | Message |
|------|---------|
| `56dd744` | (see git log) |
| `ca32495` | (see git log) |
| `e03ae44` | (see git log) |
| `1c3e843` | (see git log) |
| `4e8b404` | (see git log) |
| `d449e9b` | (see git log) |
| `7418262` | (see git log) |

### Testing

- [OK] Release worktree full suite on the Neon test branch: 10 failed (pre-existing; origin/main baseline 11) / 881 passed / 9 skipped
- [OK] Alembic: one head; empty-database upgrade/current/check on the test branch passes
- [OK] Production-path 30-case rerun: all succeed within one retry
- [OK] Production R6: alembic=d04fdae36884; no HNSW; largest tenant 50/50 (was 12-39), ~100 ms; health 200

### Status

[OK] **Completed**

### Next Steps

- Merge main back into dev (conflicts expected; see the PR #25 notes)
- Follow-ups: diversify off for tenant-wide search; OR/RRF re-eval; source-block guard aborts; async-generator leak


## Session 23: Neon idle scale-to-zero: Redis-gated notification repair

**Date**: 2026-10-16
**Task**: Neon idle scale-to-zero: Redis-gated notification repair
**Branch**: `dev`

### Summary

Neon compute woke every 10 min (600s repair sweep vs 300s suspend), ~50% awake idle, ~3 CU-h/day. Gated the repair tick on a disposable Redis due-index, added a daily ungated full repair, made trash purge daily. Shipped to main via PR #28 (be6fc3b, merge fa9c57e).

### Main Changes

- New app/ingest/notification_repair_index.py: ZSET due index (ZADD LT, Lua compare-and-set), baseline key, repair_due_at derivation
- Repair tick issues zero PostgreSQL statements when nothing is due (heartbeat idle_skipped=1); fails open to the old sweep if Redis is unreadable
- Daily repair_all_ingest_notifications_task rebuilds the index; TRASH_PURGE_INTERVAL_SECONDS default 3600 -> 86400
- Local .env INGEST_NOTIFICATION_INTERVAL_SECONDS 10 -> 600 (not committed)

### Git Commits

| Hash | Message |
|------|---------|
| `21a51f9` | (see git log) |
| `c7e385c` | (see git log) |

### Testing

- [OK] 369 passed on release branch against Neon test branch; 2 pre-existing date-dependent failures in test_item_management_tools also fail on main

### Status

[OK] **Completed**

### Next Steps

- Production: verify REDIS_URL, drop TRASH_PURGE_INTERVAL_SECONDS=3600 in /etc/notebook-agent/notebook-agent.env; check idle_skipped heartbeats and Neon Last-day view
- main has meeting-QA fixes (PR #25) not committed on dev; they sit as uncommitted WIP under WeMM work in the shared checkout -- merge main into dev once that WIP is committed


## Session 24: Residential video egress: Evomi for YouTube, Bilibili via US_VIDEO_PROXY_URL

**Date**: 2026-10-16
**Task**: Residential video egress: Evomi for YouTube, Bilibili via US_VIDEO_PROXY_URL
**Branch**: `dev`

### Summary

Production YouTube was blocked from the OVH datacenter IP. Added a server-local tinyproxy egress (loopback, YouTube/Bilibili-only filter, LoadCredential + AppArmor override) forwarding to Evomi Core Residential (sticky, US), installed and canaried it in production, then routed Bilibili (HTTP 412 from prod) and all server-side video fetches through it under the renamed US_VIDEO_PROXY_URL.

### Main Changes

- deploy/youtube-egress tinyproxy config, filter, AppArmor override; notebook-agent-youtube-egress.service; runbook youtube-residential-egress.md
- Production: tinyproxy 1.11.3 installed, unit enabled, Evomi credentials entered by the user via getpass helper; YouTube canary passed
- app/connectors/proxy_env.py shared child-proxy env; BilibiliConnector proxy + 412 classification; YOUTUBE_PROXY_URL renamed to US_VIDEO_PROXY_URL (old name is a startup error); filter adds Bilibili hosts; LogLevel Notice

### Git Commits

| Hash | Message |
|------|---------|
| `b05691d` | (see git log) |
| `a8ca494` | (see git log) |
| `6270972` | (see git log) |
| `a7fe340` | (see git log) |
| `7d069a9` | (see git log) |

### Testing

- [OK] Targeted 324-328 passed (worktree/shared/release); full suite failures identical to unmodified main/dev baselines
- [OK] Local tinyproxy 18/18 runtime cases; end-to-end Bilibili fetch_meta + ASR audio through new filter; prod YouTube connector canary

### Status

[OK] **Completed**

### Next Steps

- Merge PR #29; rename YOUTUBE_PROXY_URL to US_VIDEO_PROXY_URL in prod env right before approving the Production gate; reinstall filter, LogLevel Notice, restart egress, Bilibili canary
- Buy Evomi Core Residential pay-as-you-go before the trial ends (~2026-10-17)


## Session 25: Close side-panel page Q&A and Bilibili Trellis tasks

**Date**: 2026-10-19
**Task**: Close side-panel page Q&A and Bilibili Trellis tasks
**Branch**: `dev`

### Summary

The side-panel page Q&A and Bilibili code was already on dev and on main (PR #32, extension 0.2.0, merged 2026-10-17), but the Trellis records were never closed. Checked every acceptance item against code and tests, ticked those that are proven, archived the two child tasks, and left the parent open because 10-15-web-companion-build has not started. lane said they only tested the extension's subtitle summary; the other live checks were not run and moved into another task, so they are recorded as not verified.

### Main Changes

- Ticked prd.md acceptance items of 10-15-side-panel-page-qa and 10-17-bilibili-side-panel with test and file evidence; added 收尾记录 and a 完成情况 table to implement.md
- Ticked the cross-child criteria in 10-15-extension-page-qa/prd.md and added a children status table
- Archived 10-15-side-panel-page-qa and 10-17-bilibili-side-panel; kept the parent 10-15-extension-page-qa open for 10-15-web-companion-build

### Git Commits

| Hash | Message |
|------|---------|
| `c6d2044` | (see git log) |
| `966dfd4` | (see git log) |
| `07fe844` | (see git log) |
| `9420cea` | (see git log) |
| `7076283` | (see git log) |

### Testing

- [OK] CI workflow Extension and API contract passed on main at 9daf552 (PR #32); read-only check of test names and code, nothing re-run
- [NOT RUN] Bilibili logged-in AI subtitles, ?p=2, 收藏第1P, NTULearn/Kaltura, first-delta timing, real-player seek, pot on two videos, log grep

### Status

[OK] **Completed**

### Next Steps

- Live checks above were moved by lane into another task (not named); confirm which one carries them
- Start 10-15-web-companion-build, or archive the parent 10-15-extension-page-qa once lane decides to drop it


## Session 26: Release dev to main (4eefa34), production deploy, reindex, extension 0.2.3 package

**Date**: 2026-10-21
**Task**: Release dev to main (4eefa34), production deploy, reindex, extension 0.2.3 package
**Branch**: `dev`

### Summary

Diagnosed the Chinese recall miss (tenant 211), shipped RRF fusion + zh bigram fts + title rows and inline citation chips, prepared and released dev to main via PR #36, fixed the host-managed deploy dispatcher, deployed 4eefa34, ran the production reindex (114/122/0), merged main back into dev, and prepared the extension 0.2.3 packages.

### Main Changes

- Retrieval: RRF fusion with two-pass window, CJK bigram fts, per-item title rows, CLI reindex (58326da)
- Chat: [S<id>] markers renumbered to [^n] for browsers; inline citation chips replace the evidence list (26d4267)
- Release readiness: website served from the extension build, WeMM quarantine, CI green, full-stack smoke, release/2026-10-dev-sync merge -> PR #36 -> main 4eefa34
- Production: replaced the stale /usr/local/sbin/notebook-agent-deploy copy, redeployed 4eefa34, migrations to f8a9b0c1d2e3, reindex via transient systemd unit, tenant 211 re-check (429/430 recalled on all four queries)
- origin/main merged back into dev (d6c91ca); extension bumped to 0.2.3 with the offline package replaced (ac7fafb); store zip built for manual upload

### Git Commits

| Hash | Message |
|------|---------|
| `58326da` | (see git log) |
| `26d4267` | (see git log) |
| `d4bc689` | (see git log) |
| `8b7c12a` | (see git log) |
| `90c55a4` | (see git log) |
| `cc903b4` | (see git log) |
| `4eefa34` | (see git log) |
| `d6c91ca` | (see git log) |
| `ac7fafb` | (see git log) |
| `697a621` | (see git log) |

### Testing

- [OK] [OK] PR #36 CI deterministic-extension-gates; local full-stack smoke on the Neon test branch; production checks (/, #/login, /privacy, health, JSON 404, zip, alembic check); reindex idempotent re-run 0/0/0
- [OK] [OK] extension 0.2.3: install, check:api, typecheck, lint, vitest 778 passed, build, audit, package:release; pytest test_linux_frontend_deployment 6 passed
- [OK] [NOT RUN] production logged-in web library/chat/video detail, extension side panel and MCP against production (need email code / token)

### Status

[OK] **Completed**

### Next Steps

- Upload extension/release/stashseek-chat-chrome-web-store-0.2.3.zip in the Chrome developer dashboard; release ac7fafb to main so the website serves the 0.2.3 offline package
- Host unit WEB_STATIC_DIR=extension/dist + daemon-reload (deferred by lane; compat link in place)
- Decide whether to archive 10-20-chrome-store-entry, 10-20-cn-recall-fusion, 10-20-inline-citation-chips (all shipped)
