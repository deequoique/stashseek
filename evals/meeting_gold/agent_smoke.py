"""R8: production-path end-to-end agent smoke harness.

Ported from the session scratch runner
(``.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/meeting_agent_prodpath.reference.py``),
adapted to reuse the shared RQ1 library (one tenant holding all 35 val
meetings, see :mod:`production_path`) instead of ingesting one isolated
tenant per meeting.

Safety: this module never calls a real provider on import, in its own tests,
or through ``--dry-run``. A real run (``--dry-run`` unset) performs real
``agent.stream()`` calls and must only be launched deliberately, against the
Neon test branch, by a caller that has already run ``prepare_test_environment``.
"""

from __future__ import annotations

import collections
import json
import logging
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import production_path
from .scoring import _validate_case

# -------------------------------------------------------- failure taxonomy
# These signatures reproduce the 2026-10-06 production-path rerun taxonomy
# (see .trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/findings-20261006.md):
# whitespace-delta stream-guard aborts, stream-plan over-citation, and
# composer-fallback fail-closed on too-many/duplicate citations. The function
# is pure and offline-testable against the recorded scratch results.
FailureCategory = str

CATEGORY_COMPLETED: FailureCategory = "completed"
CATEGORY_STREAM_GUARD_WHITESPACE: FailureCategory = "stream_guard_whitespace"
CATEGORY_STREAM_PLAN_OVER_CITE: FailureCategory = "stream_plan_over_cite"
CATEGORY_COMPOSER_FALLBACK: FailureCategory = "composer_fallback_fail_closed"
CATEGORY_OTHER_FAILED: FailureCategory = "other_failed"

_COMPOSER_FALLBACK_REASONS = frozenset({"too_many_segments", "duplicate_citation"})


def categorize_failure(record: Mapping[str, Any]) -> FailureCategory:
    """Classify one case's recorded outcome into a fixed failure taxonomy.

    ``record`` uses the same shape as a ``run_case`` payload (and the frozen
    2026-10-06 scratch results): ``answer_status``, ``guard_rejections``,
    ``stream_failures``, ``section_aborted``, ``citation_failures``.
    """

    if record.get("answer_status") == "completed":
        return CATEGORY_COMPLETED

    guard_rejections = record.get("guard_rejections") or []
    stream_failures = record.get("stream_failures") or []
    citation_failures = record.get("citation_failures") or []

    whitespace_guard = any("empty" in (g.get("rule") or "") for g in guard_rejections)
    unsafe_streamed = any(
        failure.get("message") == "unsafe streamed text" for failure in stream_failures
    )
    if whitespace_guard and unsafe_streamed:
        return CATEGORY_STREAM_GUARD_WHITESPACE

    if any("ToolRetryError" in (failure.get("cause") or "") for failure in stream_failures):
        return CATEGORY_STREAM_PLAN_OVER_CITE

    if (
        not stream_failures
        and not guard_rejections
        and not record.get("section_aborted")
        and any(reason in _COMPOSER_FALLBACK_REASONS for reason in citation_failures)
    ):
        return CATEGORY_COMPOSER_FALLBACK

    return CATEGORY_OTHER_FAILED


# --------------------------------------------------------------- services
def _build_recording_services_class() -> type:
    from app.agent.services import KnowledgeServices

    class RecordingServices(KnowledgeServices):
        def __init__(self, *args: Any, trace: list, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.trace = trace

        def _record(
            self,
            tool: str,
            *,
            query: str | None = None,
            args: dict | None = None,
            results: list,
            status: str = "succeeded",
            error_type: str | None = None,
        ) -> None:
            self.trace.append(
                {
                    "tool": tool,
                    "query": query,
                    "args": args,
                    "status": status,
                    "error_type": error_type,
                    "results": [
                        {
                            "segment_id": c.segment_id,
                            "item_id": c.item_id,
                            "score": getattr(c, "_retrieval_score", None),
                            "excerpt": c.excerpt,
                        }
                        for c in results
                    ],
                }
            )

        def search_segments(self, query: str, *, limit: int = 10, item_id: int | None = None):
            try:
                result = super().search_segments(query, limit=limit, item_id=item_id)
            except Exception as exc:
                self._record(
                    "search_segments",
                    query=query,
                    args={"limit": limit, "item_id": item_id},
                    results=[],
                    status="failed",
                    error_type=type(exc).__name__,
                )
                raise
            self._record(
                "search_segments",
                query=query,
                args={"limit": limit, "item_id": item_id},
                results=result,
            )
            return result

        def get_neighbors(self, segment_id: int, *, radius: int = 1):
            try:
                result = super().get_neighbors(segment_id, radius=radius)
            except Exception as exc:
                self._record(
                    "get_neighbors",
                    args={"segment_id": segment_id, "radius": radius},
                    results=[],
                    status="failed",
                    error_type=type(exc).__name__,
                )
                raise
            self._record(
                "get_neighbors", args={"segment_id": segment_id, "radius": radius}, results=result
            )
            return result

        def open_at(self, segment_id: int):
            try:
                result = super().open_at(segment_id)
            except Exception as exc:
                self._record(
                    "open_at",
                    args={"segment_id": segment_id},
                    results=[],
                    status="failed",
                    error_type=type(exc).__name__,
                )
                raise
            self._record("open_at", args={"segment_id": segment_id}, results=[result])
            return result

    return RecordingServices


# ---------------------------------------------------------------- metrics
def chunk_metrics(
    ranked_sids: list[list[str]], groups: list[set], gold: set, words: list[int]
) -> dict[str, Any]:
    """Any-overlap chunk-level retrieval metrics (matches the scratch runner)."""

    out: dict[str, Any] = {}
    for k in (1, 3, 5, 10):
        top = set().union(*ranked_sids[:k]) if ranked_sids else set()
        out[f"chunk_hit@{k}"] = float(bool(top & gold))
        out[f"chunk_recall@{k}"] = len(top & gold) / len(gold) if gold else 0.0
        out[f"chunk_group_any@{k}"] = (
            sum(bool(top & g) for g in groups) / len(groups) if groups else 0.0
        )
    all_sids = set().union(*ranked_sids) if ranked_sids else set()
    out["pool_chunks"] = len(ranked_sids)
    out["pool_words"] = sum(words)
    out["pool_hit"] = float(bool(all_sids & gold))
    out["pool_recall"] = len(all_sids & gold) / len(gold) if gold else 0.0
    return out


def citation_metrics(cited_sids: list[list[str]], groups: list[set], gold: set) -> dict[str, Any]:
    all_sids = set().union(*cited_sids) if cited_sids else set()
    return {
        "cited_chunks": len(cited_sids),
        "cited_chunk_precision": (
            (sum(bool(set(s) & gold) for s in cited_sids) / len(cited_sids)) if cited_sids else 0.0
        ),
        "cited_gold_recall": (len(all_sids & gold) / len(gold)) if gold else 0.0,
        "cited_group_any": (sum(bool(all_sids & g) for g in groups) / len(groups)) if groups else 0.0,
        "cited_any_gold": float(bool(all_sids & gold)),
    }


def _one_case_selection(bundle: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    from .artifacts import (
        _selection_hash_payload,
        _selection_shortfalls,
        select_cases,
        validate_selection,
    )
    from .core import canonical_hash

    selection = select_cases(
        bundle["cases"],
        dataset_hash=bundle["dataset_hash"],
        split="val",
        limit=1,
        seed=0,
        leakage=bundle["audit"]["leakage"],
    )
    selection["selected_case_ids"] = [case_id]
    target = next(c for c in bundle["cases"] if c.case_id == case_id)
    selection["shortfalls"] = _selection_shortfalls(
        [c for c in bundle["cases"] if c.split == "val"], [target]
    )
    selection["selection_hash"] = canonical_hash(_selection_hash_payload(selection))
    validate_selection(
        selection, bundle["cases"], dataset_hash=bundle["dataset_hash"], leakage=bundle["audit"]["leakage"]
    )
    return {
        key: selection[key]
        for key in (
            "selection_version",
            "dataset_hash",
            "split",
            "selected_case_ids",
            "seed",
            "selection_policy",
            "selection_hash",
        )
    }


async def run_case(
    case: Any,
    meeting_info: Mapping[str, Any],
    *,
    agent: Any,
    settings: Any,
    factory: Any,
    agent_embedder: Any,
    provider_responses: list,
    bundle: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    from .integration import map_chunk_records
    from .scoring import score_cases

    RecordingServices = _build_recording_services_class()
    from app.agent.context import TurnContext
    from app.agent.types import AgentRequest
    from app.channels.types import TenantContext
    from app.diagnostics import LOGGER, RequestDiagnostics

    seg_by_id = {
        segment["id"]: {
            **segment,
            "sids": list(
                dict.fromkeys([*segment["full_sentence_ids"], *segment["partial_sentence_ids"]])
            ),
        }
        for segment in meeting_info["segments"]
    }

    trace: list = []
    run_tag = uuid.uuid4().hex[:12]
    agent._service_factory = lambda request: RecordingServices(
        request.tenant, factory, embedder=agent_embedder, trace=trace
    )
    request = AgentRequest(
        question=case.query,
        tenant=TenantContext(meeting_info["tenant_user_id"], 1, "web", "eval", f"eval-{run_tag}"),
        thread_db_id=1,
        thread_public_id=f"eval-{run_tag}",
        message_id=f"m-{run_tag}",
        request_id=f"r-{run_tag}",
        history=(),
        context=TurnContext(),
    )
    records: list[dict[str, Any]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            payload = getattr(record, "diagnostic_payload", None)
            if isinstance(payload, dict):
                records.append(dict(payload))

    handler = Capture()
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    diagnostics = RequestDiagnostics.start(
        request.request_id,
        meeting_info["tenant_user_id"],
        environment="development",
        allow_retrieval_content=False,
    )
    provider_responses.clear()

    # Instrumentation only: record *why* the stream path failed (exception
    # class/message and the validator rule), never the model prose itself.
    from app.agent import answer_pipeline as ap
    from app.agent.answer_validation import _CITATION_LIKE_MARKER, _VALID_MARKER

    stream_failures: list[dict[str, Any]] = []
    guard_rejections: list[dict[str, Any]] = []
    aborted: list[Any] = []
    original_failure = ap.AnswerPipeline.__dict__["_stream_failure"].__func__

    def patched_failure(req: Any, code: str, text: str) -> Any:
        exc = sys.exc_info()[1]
        cause = exc.__cause__ if exc is not None else None
        stream_failures.append(
            {
                "code": code,
                "exception": type(exc).__name__ if exc else None,
                "message": str(exc)[:160] if exc else None,
                "cause": f"{type(cause).__name__}: {str(cause)[:160]}" if cause else None,
            }
        )
        return original_failure(req, code, text)

    ap.AnswerPipeline._stream_failure = staticmethod(patched_failure)
    original_forbidden = ap._StreamingTextGuard.__dict__["_has_forbidden_text"].__func__

    def patched_forbidden(value: str) -> bool:
        try:
            ap.validate_natural_answer(value)
        except ap.NaturalAnswerValidationError as exc:
            markers = [
                m.group(0)[:24]
                for m in _CITATION_LIKE_MARKER.finditer(value)
                if _VALID_MARKER.fullmatch(m.group(0)) is None
            ]
            guard_rejections.append(
                {
                    "rule": str(exc),
                    "malformed_markers": markers[:3],
                    "valid_markers": len(_VALID_MARKER.findall(value)),
                }
            )
            return True
        return False

    ap._StreamingTextGuard._has_forbidden_text = staticmethod(patched_forbidden)
    events: collections.Counter = collections.Counter()
    final = None
    started = time.monotonic()
    try:
        async for event in agent.stream(request, diagnostics=diagnostics):
            events[event.type] += 1
            if event.type == "section_aborted":
                aborted.append(event.reason)
            if event.type == "completed":
                final = event
    finally:
        LOGGER.removeHandler(handler)
        ap.AnswerPipeline._stream_failure = staticmethod(original_failure)
        ap._StreamingTextGuard._has_forbidden_text = staticmethod(original_forbidden)
    elapsed = round(time.monotonic() - started, 1)
    answer = final.answer if final is not None else None

    pool: dict[int, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    order = 0
    for event_index, event in enumerate(trace):
        for hit in event["results"]:
            seg = seg_by_id.get(hit["segment_id"])
            if seg is None or hit["excerpt"] != seg["text"]:
                rejected.append({"event_index": event_index, "segment_id": hit["segment_id"]})
                continue
            prev = pool.get(hit["segment_id"])
            score = hit["score"]
            if prev is None:
                pool[hit["segment_id"]] = {"first_seen": order, "score": score}
                order += 1
            elif score is not None and (prev["score"] is None or score > prev["score"]):
                prev["score"] = score
    ranked = sorted(
        pool.items(), key=lambda kv: (kv[1]["score"] is None, -(kv[1]["score"] or 0.0), kv[1]["first_seen"])
    )
    chunk_records = [
        {
            "rank": r,
            "chunk_id": str(sid),
            "meeting_id": case.meeting_id,
            "source_sentence_ids": seg_by_id[sid]["sids"],
            "text": seg_by_id[sid]["text"],
        }
        for r, (sid, _) in enumerate(ranked, 1)
    ]
    mapped = (
        map_chunk_records(chunk_records, bundle["corpus"], meeting_id=case.meeting_id)
        if chunk_records
        else {"retrieved_sentence_ids": [], "status": "complete"}
    )
    cited = list(answer.citations) if answer is not None else []
    citation_records = [
        {
            "rank": r,
            "chunk_id": str(c.segment_id),
            "meeting_id": case.meeting_id,
            "source_sentence_ids": seg_by_id[c.segment_id]["sids"],
            "text": seg_by_id[c.segment_id]["text"],
        }
        for r, c in enumerate(cited, 1)
        if c.segment_id in seg_by_id
    ]
    mapped_cited = (
        map_chunk_records(citation_records, bundle["corpus"], meeting_id=case.meeting_id)
        if citation_records
        else {"retrieved_sentence_ids": [], "status": "complete"}
    )

    succeeded = sum(e["status"] == "succeeded" for e in trace)
    failed = sum(e["status"] == "failed" for e in trace)
    retrieval_status = "failed" if not succeeded else "partial" if (failed or rejected) else "completed"
    retrieval_completed = retrieval_status == "completed"
    answer_completed = (
        retrieval_completed
        and answer is not None
        and answer.status in {"ok", "not_found"}
        and answer.error_code is None
        and len(citation_records) == len(cited)
    )

    selection = _one_case_selection(bundle, case.case_id)
    record = {
        "case_id": case.case_id,
        "retrieved_sentence_ids": mapped["retrieved_sentence_ids"],
        "cited_sentence_ids": mapped_cited["retrieved_sentence_ids"],
    }
    base = {
        "schema_version": "meeting-gold-predictions-v1",
        "dataset_hash": bundle["dataset_hash"],
        "selection_hash": selection["selection_hash"],
    }
    sentence_report: dict[str, Any] = {}
    for stage, include, names in (
        (
            "retrieval",
            retrieval_completed,
            ("retrieval_recall@1", "retrieval_recall@3", "retrieval_recall@5", "retrieval_mrr", "retrieval_hit@5"),
        ),
        (
            "citation",
            answer_completed,
            ("gold_evidence_recall", "gold_citation_precision", "reference_group_any_hit", "reference_group_all_hit"),
        ),
    ):
        raw = score_cases(
            [case],
            {**base, "records": [record] if include else []},
            corpus=bundle["corpus"],
            selection=selection,
            dataset_hash=bundle["dataset_hash"],
            mode="complete",
        )
        agg = raw.get("aggregates")
        sentence_report[stage] = {
            "status": raw["status"],
            "eligible": raw["coverage"]["eligible_count"],
            "metrics": {n: agg["query_macro"]["metrics"][n] for n in names} if agg else None,
        }

    eligible = case.eligibility["ces"] == "citation_scorable"
    chunk_report = None
    if eligible:
        known = {s.id: s for s in bundle["corpus"][case.meeting_id]}
        validated = _validate_case(case, known_sentences=known, corpus_meetings={case.meeting_id}, policy="ces")
        groups = [set(g) for g in validated.groups]
        gold = set(validated.gold_ids)
        chunk_report = {
            "retrieval": chunk_metrics(
                [seg_by_id[sid]["sids"] for sid, _ in ranked],
                groups,
                gold,
                [seg_by_id[sid]["words"] for sid, _ in ranked],
            )
            if retrieval_completed
            else None,
            "citation": citation_metrics(
                [seg_by_id[c.segment_id]["sids"] for c in cited if c.segment_id in seg_by_id],
                groups,
                gold,
            )
            if answer_completed
            else None,
            "gold_chunks": sum(bool(set(s["sids"]) & gold) for s in seg_by_id.values()),
        }

    tool_events = collections.Counter(
        (r.get("tool_name"), r.get("tool_outcome")) for r in records if r.get("stage") == "tool_call"
    )
    payload = {
        "case_id": case.case_id,
        "meeting_id": case.meeting_id,
        "domain": case.domain,
        "query": case.query,
        "item_id": meeting_info["item_id"],
        "user_id": meeting_info["tenant_user_id"],
        "status": "completed" if answer_completed else "incomplete",
        "retrieval_status": retrieval_status,
        "answer_status": "completed" if answer_completed else "failed",
        "agent_status": getattr(answer, "status", None),
        "agent_error_code": getattr(answer, "error_code", None),
        "agent_citation_count": len(cited),
        "elapsed_seconds": elapsed,
        "stream_events": dict(events),
        "retrieval_trace": [
            {
                "tool": e["tool"],
                "query": e["query"],
                "args": e["args"],
                "status": e["status"],
                "error_type": e["error_type"],
                "segment_ids": [h["segment_id"] for h in e["results"]],
                "scores": [h["score"] for h in e["results"]],
            }
            for e in trace
        ],
        "tool_events": {f"{k[0]}:{k[1]}": v for k, v in tool_events.items()},
        "limits": [{"kind": r.get("limit_kind"), "phase": r.get("agent_phase")} for r in records if r.get("limit_kind")],
        "citation_failures": [
            r.get("failure_reason") for r in records if r.get("stage") == "citation_validated" and r.get("failure_reason")
        ],
        "final_citations": [
            {"segment_id": c.segment_id, "words": seg_by_id.get(c.segment_id, {}).get("words")} for c in cited
        ],
        "provider_responses": list(provider_responses),
        "rejected_hits": rejected,
        "sentence_report": sentence_report,
        "chunk_report": chunk_report,
        "ces_eligible": eligible,
        "stream_failures": stream_failures,
        "guard_rejections": guard_rejections,
        "section_aborted": aborted,
    }
    payload["failure_category"] = categorize_failure(payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{case.case_id.replace(':', '_')}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    )
    return payload


async def main_async(
    bundle: Mapping[str, Any],
    case_ids: Sequence[str],
    manifest: Mapping[str, Any],
    factory: Any,
    settings: Any,
    out_dir: Path,
) -> list[dict[str, Any]]:
    from app.agent.actions import AgentActionServices
    from app.agent.management import KnowledgeItemManagementService
    from app.agent.provider import build_model, model_supports_streaming
    from app.agent.runtime import KnowledgeAgent
    from app.bootstrap import build_embedding_provider
    from app.channels.pending_actions import PendingConfirmationService
    from app.ingest.submission import build_ingest_submission_service
    from app.tls import configure_trusted_ca

    import httpx

    trusted_ca = configure_trusted_ca(settings.tls_ca_bundle)
    agent_embedder = build_embedding_provider(settings, trusted_ca=trusted_ca)
    provider_responses: list = []

    async def capture(response: Any) -> None:
        if response.request.url.path.endswith("/chat/completions"):
            provider_responses.append({"status": response.status_code})

    model = build_model(settings)
    client = httpx.AsyncClient(event_hooks={"response": [capture]}, timeout=httpx.Timeout(60.0))
    provider = getattr(model, "_provider", None)
    if provider is not None:
        provider._set_http_client(client)
    stream_model = model if model_supports_streaming(model) else None
    action_services = AgentActionServices(
        submission=build_ingest_submission_service(factory, lambda *a, **k: None, settings),
        pending=PendingConfirmationService(factory),
        management=KnowledgeItemManagementService(factory, retention_days=settings.trash_retention_days),
    )
    agent = KnowledgeAgent(
        model,
        settings,
        lambda r: None,
        action_factory=lambda _r: action_services,
        stream_model=stream_model,
        multimodal_embedding_provider=None,
    )

    cases = {c.case_id: c for c in bundle["cases"]}
    tenant_user_id = manifest["tenant_user_id"]
    results: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = cases[case_id]
        meeting_info = dict(manifest["meetings"][case.meeting_id])
        meeting_info["tenant_user_id"] = tenant_user_id
        try:
            result = await run_case(
                case,
                meeting_info,
                agent=agent,
                settings=settings,
                factory=factory,
                agent_embedder=agent_embedder,
                provider_responses=provider_responses,
                bundle=bundle,
                out_dir=out_dir,
            )
        except Exception as exc:  # keep going; record the harness failure explicitly
            result = {
                "case_id": case_id,
                "status": "harness_error",
                "error": f"{type(exc).__name__}: {exc}",
                "failure_category": "harness_error",
            }
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{case_id.replace(':', '_')}.json").write_text(json.dumps(result, indent=1))
        results.append(result)
    await client.aclose()
    return results


def _dry_run_case(bundle: Mapping[str, Any], case_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate case/meeting/request wiring without any provider call."""

    from app.agent.context import TurnContext
    from app.agent.types import AgentRequest
    from app.channels.types import TenantContext

    cases = {c.case_id: c for c in bundle["cases"]}
    case = cases[case_id]
    meeting_info = manifest["meetings"][case.meeting_id]
    request = AgentRequest(
        question=case.query,
        tenant=TenantContext(manifest["tenant_user_id"], 1, "web", "eval", "eval-dry-run"),
        thread_db_id=1,
        thread_public_id="eval-dry-run",
        message_id="m-dry-run",
        request_id="r-dry-run",
        history=(),
        context=TurnContext(),
    )
    return {
        "case_id": case_id,
        "status": "dry_run_ok",
        "meeting_id": case.meeting_id,
        "item_id": meeting_info["item_id"],
        "segments": len(meeting_info["segments"]),
        "question_len": len(request.question),
    }


def run_smoke(
    bundle: Mapping[str, Any],
    case_ids: Sequence[str],
    run_dir: Path,
    *,
    settings: Any,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reuse (or build) the shared RQ1 library, then run the agent per case."""

    import asyncio

    production_path.assert_test_environment_active()
    # Matches retrieval_study.run_rq1's library (D2: all 35 val meetings in
    # one tenant), so an agent-smoke run can reuse an rq1 run's library
    # directory unchanged.
    meeting_ids = production_path.val_meeting_ids(bundle)
    manifest = production_path.ingest_library(bundle, meeting_ids, run_dir, settings=settings)

    if dry_run:
        results = [_dry_run_case(bundle, case_id, manifest) for case_id in case_ids]
        return {"dry_run": True, "results": results}

    factory = production_path.session_factory_from_manifest()
    results = asyncio.run(main_async(bundle, case_ids, manifest, factory, settings, run_dir))
    categories = collections.Counter(categorize_failure(r) for r in results if "failure_category" not in r) \
        or collections.Counter(r.get("failure_category", "harness_error") for r in results)
    return {"dry_run": False, "results": results, "failure_categories": dict(categories)}


__all__ = [
    "CATEGORY_COMPLETED",
    "CATEGORY_COMPOSER_FALLBACK",
    "CATEGORY_OTHER_FAILED",
    "CATEGORY_STREAM_GUARD_WHITESPACE",
    "CATEGORY_STREAM_PLAN_OVER_CITE",
    "categorize_failure",
    "chunk_metrics",
    "citation_metrics",
    "main_async",
    "run_case",
    "run_smoke",
]
