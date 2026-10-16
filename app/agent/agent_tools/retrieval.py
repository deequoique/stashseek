"""Retrieval and evidence-expansion tool registration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict
import time
from typing import Annotated

from pydantic import Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from app.agent.autonomy import ErrorEnvelope, RecoveryGrant
from app.agent.limits import (
    INSPECT_MEDIA_CALLS_LIMIT,
    INSPECT_MEDIA_CANDIDATE_LIMIT,
    INSPECT_MEDIA_TIMEOUT_SECONDS,
    SEARCH_RESULT_LIMIT,
)
from app.agent.media import (
    MediaInspectionRequest,
    MediaInspectionResult,
    MediaInspectionValidationError,
    MediaState,
    MediaEvidence,
)
from app.agent.runtime_state import (
    AgentDeps,
    NORMAL_RETRIEVAL_CALLS_LIMIT,
    NORMAL_SEARCH_CALLS_LIMIT,
    RetrievalKind,
    _RecoveryPayload,
    _citation_matches_scope,
)
from app.agent.agent_tools.policy import ToolPolicy
from app.agent.types import (
    RetrievalToolPayload,
    UnifiedRetrievalCandidate,
    UnifiedRetrievalResult,
)
from app.retrieval.media import MediaRetrievalService, MediaSearchResult
from app.ingest.submission import normalize_item_reference


def _item_details_matches_scope(
    result: dict,
    scope: tuple[tuple[str, str], ...],
) -> bool:
    try:
        reference = normalize_item_reference(str(result.get("url", "")))
    except (ValueError, TypeError):
        return False
    return (
        result.get("platform") == reference.platform
        and (reference.platform, reference.platform_id) in set(scope)
    )


def register_retrieval_tools(
    agent: Agent,
    policy: ToolPolicy,
    *,
    multimodal_retrieval_enabled: bool = False,
    media_search_service=None,
) -> None:
    def _run_search_segments(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = SEARCH_RESULT_LIMIT,
        item_id: int | None = None,
    ) -> RetrievalToolPayload:
        """Execute a tenant-bound search, optionally narrowed to an observation."""

        if item_id is not None and not ctx.deps.can_scope_search_to_item(item_id):
            # This is only a syntactic guard.  Ownership and readiness are
            # always enforced again by the tenant-bound service query; an
            # item need not have been observed by a preceding tool call.
            ctx.deps.invalid_item_scope_attempt = True
            raise ModelRetry(
                "item_id 必须是正整数；条目归属和可检索状态由服务器校验。"
            )
        normalized_query = query.strip()
        arguments = {
            "query": normalized_query,
            "limit": int(limit),
            "item_id": item_id,
        }
        fingerprint = (
            ctx.deps.recovery_ledger.fingerprint_read(
                "search_segments", arguments
            )
            if ctx.deps.recovery_ledger is not None
            else None
        )
        if fingerprint is not None:
            if fingerprint == ctx.deps.last_empty_search_fingerprint:
                # Repeating an empty query is still the same observation, not
                # a reformulation and not a backend call.
                return {
                    "status": "ok",
                    "evidence": [],
                    "reason": None,
                }
            if ctx.deps.last_empty_search_fingerprint is not None:
                recovery_policy = ctx.deps.recovery_policy
                ledger = ctx.deps.recovery_ledger
                grant = (
                    recovery_policy.grant_for_empty_search(
                        search_budget_remaining=(
                            NORMAL_SEARCH_CALLS_LIMIT - ctx.deps.search_calls
                        ),
                        retrieval_budget_remaining=(
                            NORMAL_RETRIEVAL_CALLS_LIMIT - ctx.deps.retrieval_calls
                        ),
                    )
                    if recovery_policy is not None
                    else RecoveryGrant((), 0)
                )
                if (
                    not grant.permits("reformulate_search")
                    or ledger is None
                    or not ledger.record_recovery(
                        "reformulate_search",
                        category="transient_read",
                    )
                ):
                    ctx.deps.read_recovery_exhausted = True
                    if recovery_policy is not None:
                        error = ErrorEnvelope.from_category(
                            "read_unavailable",
                            operation="read",
                            code="retry_not_allowed",
                            partial_evidence=bool(ctx.deps.citations),
                        )
                        policy._recovery_diagnostic(
                            ctx.deps,
                            error=error,
                            grant=grant,
                            outcome="exhausted",
                        )
                    return {
                        "status": "ok",
                        "evidence": [],
                        "reason": None,
                    }
                error = ErrorEnvelope.from_category(
                    "transient_read",
                    operation="read",
                    partial_evidence=bool(ctx.deps.citations),
                )
                policy._recovery_diagnostic(
                    ctx.deps,
                    error=error,
                    grant=grant,
                    action="reformulate_search",
                    outcome="consumed",
                )

        pending_same = (
            fingerprint is not None
            and fingerprint in ctx.deps.pending_read_failures
        )
        retry_already_used = bool(
            pending_same
            and ctx.deps.recovery_ledger is not None
            and ctx.deps.recovery_ledger.same_read_retry_count(fingerprint) >= 1
        )
        pending_other = (
            any(
                tool_name == "search_segments"
                for tool_name in ctx.deps.pending_read_failures.values()
            )
            and not pending_same
        )
        if not (retry_already_used or pending_other):
            if skipped := policy.skipped_payload(
                ctx,
                "search_segments",
                RetrievalKind.SEARCH,
            ):
                return skipped

        if item_id is None:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit
            )
        else:
            operation = lambda: ctx.deps.services.search_segments(
                normalized_query, limit=limit, item_id=item_id
            )
        read_result = policy._read_failure(
            ctx,
            tool_name="search_segments",
            arguments=arguments,
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citations, call_index = read_result
        ctx.deps.record(citations)
        citations = [
            citation
            for citation in citations
            if not ctx.deps.reference_scope
            or _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ]
        ctx.deps.last_retrieval_result = UnifiedRetrievalResult(
            candidates=tuple(
                UnifiedRetrievalCandidate(
                    candidate_id=f"segment:{citation.segment_id}",
                    kind="text",
                    score=float(getattr(citation, "_retrieval_score", 0.0) or 0.0),
                    item_id=citation.item_id,
                    segment_id=citation.segment_id,
                    start_sec=citation.start_sec,
                )
                for citation in citations
            ),
            source="text",
        )
        ctx.deps.tool_event(
            "search_segments", "succeeded", call_index, len(citations)
        )
        if diagnostics := ctx.deps.diagnostics:
            diagnostics.retrieval_detail(
                tool_name="search_segments",
                call_index=call_index,
                query=query,
                limit=limit,
            )
            for citation in citations:
                diagnostics.retrieval_detail(
                    tool_name="search_segments",
                    call_index=call_index,
                    query=query,
                    limit=limit,
                    item_id=citation.item_id,
                    segment_id=citation.segment_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    score=getattr(citation, "_retrieval_score", None),
                    start=citation.start_sec,
                )
        if ctx.deps.recovery_ledger is not None:
            if citations:
                ctx.deps.last_empty_search_fingerprint = None
            elif fingerprint is not None:
                ctx.deps.last_empty_search_fingerprint = fingerprint
        # A reservation is not evidence of a completed read.  This marker is
        # set only after the backend returned, including an empty result.
        ctx.deps.successful_searches += 1
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_search)
    def search_segments(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = SEARCH_RESULT_LIMIT,
        item_id: Annotated[int | None, Field(gt=0)] = None,
    ) -> dict:
        """Search the tenant library globally or within an optional item."""

        return _run_search_segments(ctx, query, limit, item_id)

    @agent.tool(prepare=policy.prepare_expansion)
    def get_neighbors(
        ctx: RunContext[AgentDeps], segment_id: int, radius: int = 1
    ) -> dict:
        """Read nearby segments around a search result."""

        if skipped := policy.skipped_payload(
            ctx,
            "get_neighbors",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.get_neighbors(segment_id, radius=radius)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="get_neighbors",
            arguments={"segment_id": int(segment_id), "radius": int(radius)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citations, call_index = read_result
        citations = citations or []
        citations = [
            citation
            for citation in citations
            if not ctx.deps.reference_scope
            or _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ]
        ctx.deps.record(citations)
        ctx.deps.tool_event(
            "get_neighbors", "succeeded", call_index, len(citations)
        )
        if diagnostics := ctx.deps.diagnostics:
            for citation in citations:
                diagnostics.retrieval_detail(
                    tool_name="get_neighbors",
                    call_index=call_index,
                    segment_id=citation.segment_id,
                    radius=radius,
                    item_id=citation.item_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    start=citation.start_sec,
                )
        return {
            "status": "ok",
            "evidence": [value.model_dump() for value in citations],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_expansion)
    def get_item(ctx: RunContext[AgentDeps], item_id: int) -> dict:
        """Read metadata for a tenant-owned knowledge item."""

        if skipped := policy.skipped_payload(
            ctx,
            "get_item",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.get_item(item_id)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="get_item",
            arguments={"item_id": int(item_id)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        details, call_index = read_result
        result = asdict(details) if details is not None else {}
        if ctx.deps.reference_scope and result and not _item_details_matches_scope(
            result, ctx.deps.reference_scope
        ):
            result = {}
        ctx.deps.tool_event(
            "get_item", "succeeded", call_index, 1 if result else 0
        )
        if diagnostics := ctx.deps.diagnostics:
            diagnostics.retrieval_detail(
                tool_name="get_item",
                call_index=call_index,
                item_id=item_id,
                title=result.get("title"),
                author=result.get("author"),
                description=result.get("description"),
                url=result.get("url"),
            )
        return {
            "status": "ok",
            "evidence": [result] if result else [],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_expansion)
    def open_at(ctx: RunContext[AgentDeps], segment_id: int) -> dict:
        """Resolve a segment to a clickable timestamp or article anchor."""

        if skipped := policy.skipped_payload(
            ctx,
            "open_at",
            RetrievalKind.EXPANSION,
        ):
            return skipped
        operation = lambda: policy.optional_knowledge(
            lambda: ctx.deps.services.open_at(segment_id)
        )
        read_result = policy._read_failure(
            ctx,
            tool_name="open_at",
            arguments={"segment_id": int(segment_id)},
            operation=operation,
        )
        if isinstance(read_result, _RecoveryPayload):
            return read_result.payload
        citation, call_index = read_result
        if (
            citation is not None
            and ctx.deps.reference_scope
            and not _citation_matches_scope(
                citation,
                ctx.deps.reference_scope,
            )
        ):
            citation = None
        if citation is not None:
            ctx.deps.record(citation)
        ctx.deps.tool_event(
            "open_at", "succeeded", call_index, 1 if citation is not None else 0
        )
        if citation is not None:
            if diagnostics := ctx.deps.diagnostics:
                diagnostics.retrieval_detail(
                    tool_name="open_at",
                    call_index=call_index,
                    segment_id=segment_id,
                    item_id=citation.item_id,
                    title=citation.title,
                    url=citation.url,
                    excerpt=citation.excerpt,
                    start=citation.start_sec,
                )
        return {
            "status": "ok",
            "evidence": [citation.model_dump()] if citation is not None else [],
            "reason": None,
        }

    @agent.tool(prepare=policy.prepare_media_inspection)
    def inspect_media(
        ctx: RunContext[AgentDeps],
        question: Annotated[str, Field(min_length=1, max_length=500)],
        media_ids: Annotated[list[str], Field(min_length=1, max_length=INSPECT_MEDIA_CANDIDATE_LIMIT)],
    ) -> dict:
        """Inspect only opaque media IDs exposed by this retrieval run."""

        if not ctx.deps.multimodal_retrieval_enabled:
            return {"status": "skipped", "reason": "disabled", "evidence": []}
        normalized_question = question.strip()
        normalized_ids = list(dict.fromkeys(media_ids))
        if not normalized_question:
            raise ModelRetry("question must not be empty")
        if len(normalized_ids) != len(media_ids):
            raise ModelRetry("media_ids must be unique")
        if len(normalized_ids) > INSPECT_MEDIA_CANDIDATE_LIMIT:
            raise ModelRetry("inspect_media accepts at most three candidates")
        manifest = ctx.deps.media_manifest
        if manifest is None:
            raise ModelRetry("media_ids must come from the current retrieval run")
        if (
            ctx.deps.active_tenant_id is None
            or manifest.tenant_id != ctx.deps.active_tenant_id
        ):
            return {
                "status": "error",
                "error": {"category": "scope", "code": "vision_scope_mismatch"},
                "evidence": [],
            }
        if (
            ctx.deps.active_retrieval_run_id is None
            or manifest.run_id != ctx.deps.active_retrieval_run_id
        ):
            return {
                "status": "error",
                "error": {"category": "scope", "code": "vision_scope_mismatch"},
                "evidence": [],
            }
        candidates = [manifest.get(media_id) for media_id in normalized_ids]
        if any(candidate is None for candidate in candidates):
            raise ModelRetry("media_ids must come from the current retrieval run")
        service = ctx.deps.vision_service
        adapter = ctx.deps.media_evidence_adapter
        if service is None or adapter is None:
            return {
                "status": "error",
                "error": {"category": "unavailable", "code": "vision_unavailable"},
                "evidence": [],
            }
        if not ctx.deps.reserve_media_inspection(INSPECT_MEDIA_CALLS_LIMIT):
            return {
                "status": "error",
                "error": {"category": "limit", "code": "vision_budget_exhausted"},
                "evidence": [],
            }

        results: list[dict] = []
        deadline = time.monotonic() + INSPECT_MEDIA_TIMEOUT_SECONDS
        try:
            inspected, call_index = policy.execute_tool(
                ctx.deps,
                "inspect_media",
                lambda: _inspect_media_candidates(
                    ctx.deps,
                    service,
                    candidates=candidates,
                    question=normalized_question,
                    adapter=adapter,
                    deadline=deadline,
                ),
            )
            for result, evidence in inspected:
                if not isinstance(result, MediaInspectionResult) or not isinstance(evidence, MediaEvidence):
                    raise TypeError("vision service returned invalid result")
                # Commit the operation result at the tool boundary. This keeps
                # finalization independent from helper side effects and works
                # for both fresh and cached inspections.
                ctx.deps.media_evidence[evidence.evidence_id] = evidence
                results.append(evidence.as_dict())
        except FutureTimeoutError:
            return {
                "status": "error",
                "error": {"category": "timeout", "code": "vision_timeout"},
                "evidence": [],
            }
        except Exception:
            # execute_tool already records provider exceptions at the tool
            # boundary; do not expose or duplicate the underlying failure.
            return {
                "status": "error",
                "error": {"category": "provider_failure", "code": "vision_failed"},
                "evidence": [],
            }
        ctx.deps.tool_event("inspect_media", "succeeded", call_index, len(results))
        return {"status": "ok", "evidence": results, "reason": None}

    if multimodal_retrieval_enabled and media_search_service is not None:
        @agent.tool(prepare=policy.prepare_search)
        def search_media(
            ctx: RunContext[AgentDeps],
            query: Annotated[str, Field(min_length=1, max_length=500)],
            limit: int = INSPECT_MEDIA_CANDIDATE_LIMIT,
        ) -> dict:
            """Search tenant-bound media candidates in the multimodal space."""

            if not ctx.deps.multimodal_retrieval_enabled:
                return {"status": "skipped", "reason": "disabled", "evidence": []}
            if skipped := policy.skipped_payload(ctx, "search_media", RetrievalKind.SEARCH):
                return skipped
            if ctx.deps.multimodal_embedding_provider is None:
                return {"status": "error", "error": {"category": "unavailable", "code": "media_provider_unavailable"}, "evidence": []}
            try:
                query_limit = max(1, min(int(limit), INSPECT_MEDIA_CANDIDATE_LIMIT))
                if type(media_search_service) is not MediaRetrievalService:
                    raise ValueError("media search requires the concrete repository wrapper")
                result, call_index = policy.execute_tool(
                    ctx.deps,
                    "search_media",
                    lambda: media_search_service.search_media(
                        tenant_id=ctx.deps.active_tenant_id or 0,
                        run_id=ctx.deps.active_retrieval_run_id or "",
                        query=query.strip(),
                        embedder=ctx.deps.multimodal_embedding_provider,
                        limit=query_limit,
                    ),
                )
                if type(result) is not MediaSearchResult:
                    raise ValueError("media search returned invalid result")
                manifest = result.manifest
                result.validate(
                    tenant_id=ctx.deps.active_tenant_id or manifest.tenant_id,
                    run_id=ctx.deps.active_retrieval_run_id or manifest.run_id,
                    limit=query_limit,
                )
                if ctx.deps.active_tenant_id not in (None, manifest.tenant_id) or ctx.deps.active_retrieval_run_id not in (None, manifest.run_id):
                    raise ValueError("media search scope mismatch")
                ctx.deps.media_manifest = manifest
                # Replacement search scopes revoke older evidence, even when
                # media IDs happen to be reused under a changed content hash.
                ctx.deps.media_evidence.clear()
                ctx.deps.media_evidence_adapter = media_search_service.adapter
                ctx.deps.last_retrieval_result = UnifiedRetrievalResult(
                    candidates=tuple(
                        UnifiedRetrievalCandidate(
                            candidate_id="M" + item.media_id, kind="media", score=score,
                            item_id=item.item_id, media_id=item.media_id,
                        )
                        for item, score in zip(manifest.candidates, result.scores)
                    ), source="multimodal",
                )
                ctx.deps.successful_searches += 1
                ctx.deps.tool_event("search_media", "succeeded", call_index, len(manifest.candidates))
                return {"status": "ok", "evidence": [
                    {"media_id": item.media_id, "item_id": item.item_id, "score": score}
                    for item, score in zip(manifest.candidates, result.scores)
                ], "reason": None}
            except Exception:
                return {"status": "error", "error": {"category": "provider_failure", "code": "media_search_failed"}, "evidence": []}


def _inspect_media_candidates(
    deps: AgentDeps,
    service,
    *,
    candidates,
    question: str,
    adapter,
    deadline: float,
) -> list[tuple[MediaInspectionResult, MediaEvidence]]:
    inspected: list[tuple[MediaInspectionResult, MediaEvidence]] = []
    for candidate in candidates:
        # Never retain evidence from a failed, stale, or negative reinspection.
        deps.media_evidence.pop("M" + candidate.media_id, None)
        try:
            value = _inspect_cached_media(
                deps,
                service,
                candidate=candidate,
                question=question,
                adapter=adapter,
                deadline=deadline,
            )
        except MediaInspectionValidationError:
            continue
        if value is not None:
            inspected.append(value)
    return inspected


def _inspect_cached_media(
    deps: AgentDeps,
    service,
    *,
    candidate,
    question: str,
    adapter,
    deadline: float,
) -> tuple[MediaInspectionResult, MediaEvidence] | None:
    cache_key = (
        candidate.tenant_id,
        candidate.run_id,
        candidate.media_id,
        candidate.content_hash,
        question,
        candidate.model_version,
        candidate.prompt_version,
    )
    result = deps.media_inspection_cache.values.get(cache_key)
    if time.monotonic() >= deadline:
        raise FutureTimeoutError()
    before = adapter.recheck(candidate, phase="before_inspection")
    if type(before) is not MediaState or not before.inspectable:
        deps.media_inspection_cache.values.pop(cache_key, None)
        raise MediaInspectionValidationError("media candidate is not inspectable")
    if result is None:
        request = MediaInspectionRequest(
            tenant_id=candidate.tenant_id,
            item_id=candidate.item_id,
            run_id=candidate.run_id,
            media_id=candidate.media_id,
            content_hash=candidate.content_hash,
            question=question,
            model_version=candidate.model_version,
            prompt_version=candidate.prompt_version,
        )
        if time.monotonic() >= deadline:
            raise FutureTimeoutError()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(service.inspect_media, request)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FutureTimeoutError()
            result = future.result(timeout=remaining)
        finally:
            if not future.done():
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
    if type(result) is not MediaInspectionResult:
        raise MediaInspectionValidationError("vision service returned invalid result")
    result.validate()
    if any(getattr(result, name) != getattr(candidate, name) for name in (
        "tenant_id", "item_id", "run_id", "media_id", "content_hash", "model_version", "prompt_version",
    )):
        raise MediaInspectionValidationError("vision result provenance mismatch")
    if not result.publishable:
        return None
    after = adapter.recheck(candidate, phase="before_publish")
    if type(after) is not MediaState or not after.inspectable:
        deps.media_inspection_cache.values.pop(cache_key, None)
        raise MediaInspectionValidationError("media candidate is not publishable")
    evidence = adapter.publish_evidence(candidate, result)
    if type(evidence) is not MediaEvidence:
        raise MediaInspectionValidationError("media adapter returned invalid evidence")
    evidence.__post_init__()
    if evidence.candidate != candidate or evidence.inspection != result:
        raise MediaInspectionValidationError("media adapter changed inspection provenance")
    if time.monotonic() >= deadline:
        raise FutureTimeoutError()
    deps.media_inspection_cache.values[cache_key] = result
    return result, evidence
