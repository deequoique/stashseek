from dataclasses import replace
import threading
import time

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.agent.limits import INSPECT_MEDIA_CALLS_LIMIT
from app.agent.media import (
    MediaCandidate,
    MediaCandidateManifest,
    MediaEvidence,
    MediaInspectionRequest,
    MediaInspectionResult,
    MediaState,
)
from app.agent.runtime import AgentDeps, build_agent


TENANT_ID = 7
RUN_ID = "run-1"
ITEM_ID = 1


def _candidate(
    media_id: str = "media-1",
    *,
    tenant_id: int = TENANT_ID,
    run_id: str = RUN_ID,
    content_hash: str = "hash-1",
    model_version: str = "vision-v1",
    prompt_version: str = "prompt-v1",
) -> MediaCandidate:
    return MediaCandidate(
        media_id=media_id,
        tenant_id=tenant_id,
        run_id=run_id,
        item_id=ITEM_ID,
        content_hash=content_hash,
        model_version=model_version,
        prompt_version=prompt_version,
    )


def _manifest(
    *candidates: MediaCandidate,
    tenant_id: int = TENANT_ID,
    run_id: str = RUN_ID,
) -> MediaCandidateManifest:
    return MediaCandidateManifest(
        tenant_id=tenant_id,
        run_id=run_id,
        candidates=tuple(candidates),
    )


class _VisionService:
    def __init__(self, result_factory=None):
        self.calls: list[MediaInspectionRequest] = []
        self._result_factory = result_factory or self._result

    def inspect_media(self, request: MediaInspectionRequest):
        self.calls.append(request)
        return self._result_factory(request)

    @staticmethod
    def _result(request: MediaInspectionRequest) -> MediaInspectionResult:
        return MediaInspectionResult(
            tenant_id=request.tenant_id,
            item_id=request.item_id,
            run_id=request.run_id,
            media_id=request.media_id,
            content_hash=request.content_hash,
            model_version=request.model_version,
            prompt_version=request.prompt_version,
            relevant=True,
            confidence=0.875,
            evidence=(f"evidence for {request.question}",),
            ocr=("visible text",),
            objects=("document",),
            actions=("opens",),
            start_sec=12.5,
            end_sec=18.0,
        )


class _MediaEvidenceAdapter:
    def __init__(self, *, state=None):
        self.state = state or MediaState(
            tenant_matches=True,
            parent_owned=True,
            deleted=False,
            archived=False,
            ready=True,
            purge_valid=True,
        )
        self.rechecks: list[tuple[str, str]] = []
        self.published: list[tuple[str, MediaInspectionResult]] = []

    def manifest_for_run(self, *, tenant_id: int, run_id: str):
        return None

    def recheck(self, candidate, *, phase):
        self.rechecks.append((candidate.media_id, phase))
        return self.state

    def publish_evidence(self, candidate, inspection):
        self.published.append((candidate.media_id, inspection))
        return MediaEvidence(
            evidence_id=f"M{candidate.media_id}",
            candidate=candidate,
            inspection=inspection,
            title="Test media",
            location=f"https://example.test/{candidate.media_id}",
        )


def _tool_and_context(
    *,
    manifest=None,
    vision_service=None,
    active_tenant_id=TENANT_ID,
    active_retrieval_run_id=RUN_ID,
    media_evidence_adapter=None,
    multimodal_retrieval_enabled=True,
):
    agent = build_agent("test")
    tool = agent._function_toolset.tools["inspect_media"]
    deps = AgentDeps(
        services=object(),
        actions=object(),
        media_manifest=manifest,
        vision_service=vision_service,
        media_evidence_adapter=media_evidence_adapter or _MediaEvidenceAdapter(),
        active_tenant_id=active_tenant_id,
        active_retrieval_run_id=active_retrieval_run_id,
        multimodal_retrieval_enabled=multimodal_retrieval_enabled,
    )
    context = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    return tool, context, deps


def test_media_manifest_enforces_tenant_run_scope_and_unique_ids():
    with pytest.raises(ValueError, match="scope"):
        MediaCandidateManifest(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            candidates=(replace(_candidate(), tenant_id=TENANT_ID + 1),),
        )
    with pytest.raises(ValueError, match="scope"):
        MediaCandidateManifest(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            candidates=(replace(_candidate(), run_id="other-run"),),
        )
    with pytest.raises(ValueError, match="unique"):
        _manifest(_candidate("media-1"), _candidate("media-1"))


def test_inspect_media_accepts_only_current_run_manifest_ids_and_passes_provenance():
    candidate = _candidate()
    service = _VisionService()
    tool, context, _deps = _tool_and_context(
        manifest=_manifest(candidate),
        vision_service=service,
        active_tenant_id=TENANT_ID,
        active_retrieval_run_id=RUN_ID,
    )

    result = tool.function(context, "  what is shown?  ", [candidate.media_id])

    assert result["status"] == "ok"
    assert service.calls == [
        MediaInspectionRequest(
            tenant_id=TENANT_ID,
            item_id=ITEM_ID,
            run_id=RUN_ID,
            media_id="media-1",
            content_hash="hash-1",
            question="what is shown?",
            model_version="vision-v1",
            prompt_version="prompt-v1",
        )
    ]
    with pytest.raises(ModelRetry, match="current retrieval run"):
        tool.function(context, "what is shown?", ["media-from-other-run"])


@pytest.mark.parametrize(
    ("active_tenant_id", "active_retrieval_run_id"),
    [
        (TENANT_ID + 1, RUN_ID),
        (TENANT_ID, "other-run"),
    ],
)
def test_inspect_media_rejects_manifest_outside_active_tenant_or_run(
    active_tenant_id, active_retrieval_run_id
):
    service = _VisionService()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate()),
        vision_service=service,
        active_tenant_id=active_tenant_id,
        active_retrieval_run_id=active_retrieval_run_id,
    )

    result = tool.function(context, "inspect this", ["media-1"])

    assert result == {
        "status": "error",
        "error": {"category": "scope", "code": "vision_scope_mismatch"},
        "evidence": [],
    }
    assert service.calls == []
    assert deps.media_inspection_calls == 0


@pytest.mark.parametrize(
    "forged_id",
    [
        "https://private.example/media.png",
        "/tmp/private-media.png",
        "data:image/png;base64,PRIVATE_CONTENT",
    ],
)
def test_inspect_media_rejects_urls_paths_and_raw_content(forged_id):
    tool, context, _deps = _tool_and_context(
        manifest=_manifest(_candidate()), vision_service=_VisionService()
    )

    with pytest.raises(ModelRetry, match="current retrieval run"):
        tool.function(context, "inspect this", [forged_id])


def test_inspect_media_rejects_duplicate_and_over_limit_candidates():
    candidates = tuple(_candidate(f"media-{index}") for index in range(1, 5))
    tool, context, _deps = _tool_and_context(
        manifest=_manifest(*candidates), vision_service=_VisionService()
    )

    with pytest.raises(ModelRetry, match="must be unique"):
        tool.function(context, "inspect this", ["media-1", "media-1"])
    with pytest.raises(ModelRetry, match="at most three"):
        tool.function(
            context,
            "inspect this",
            ["media-1", "media-2", "media-3", "media-4"],
        )


def test_inspect_media_invokes_injected_service_and_returns_structured_evidence():
    service = _VisionService()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate("media-1"), _candidate("media-2")),
        vision_service=service,
    )

    result = tool.function(context, "find the document", ["media-1", "media-2"])

    assert result == {
        "status": "ok",
        "evidence": [
            {
                "evidence_id": "Mmedia-1",
                "media_id": "media-1",
                "item_id": ITEM_ID,
                "content_hash": "hash-1",
                "title": "Test media",
                "location": "https://example.test/media-1",
                "relevant": True,
                "confidence": 0.875,
                "evidence": ["evidence for find the document"],
                "ocr": ["visible text"],
                "objects": ["document"],
                "actions": ["opens"],
                "start_sec": 12.5,
                "end_sec": 18.0,
            },
            {
                "evidence_id": "Mmedia-2",
                "media_id": "media-2",
                "item_id": ITEM_ID,
                "content_hash": "hash-1",
                "title": "Test media",
                "location": "https://example.test/media-2",
                "relevant": True,
                "confidence": 0.875,
                "evidence": ["evidence for find the document"],
                "ocr": ["visible text"],
                "objects": ["document"],
                "actions": ["opens"],
                "start_sec": 12.5,
                "end_sec": 18.0,
            },
        ],
        "reason": None,
    }
    assert [request.media_id for request in service.calls] == ["media-1", "media-2"]
    assert [observation.outcome for observation in deps.tool_observations] == [
        "started",
        "succeeded",
    ]


def test_inspect_media_reuses_turn_local_cache_for_normalized_question():
    service = _VisionService()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate()), vision_service=service
    )

    first = tool.function(context, "  same question  ", ["media-1"])
    second = tool.function(context, "same question", ["media-1"])

    assert first == second
    assert len(service.calls) == 1
    assert len(deps.media_inspection_cache.values) == 1


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("tenant_id", TENANT_ID + 1),
        ("run_id", "changed-run"),
        ("content_hash", "changed-hash"),
        ("model_version", "changed-model"),
        ("prompt_version", "changed-prompt"),
    ],
)
def test_inspect_media_cache_key_includes_scope_and_provider_dimensions(
    changed_field, changed_value
):
    first_candidate = _candidate()
    second_candidate = replace(
        first_candidate,
        **{changed_field: changed_value},
    )
    service = _VisionService()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(first_candidate), vision_service=service
    )

    first = tool.function(context, "same question", ["media-1"])
    deps.media_manifest = _manifest(
        second_candidate,
        tenant_id=second_candidate.tenant_id,
        run_id=second_candidate.run_id,
    )
    # The active scope follows the manifest so only the cache key differs.
    deps.active_tenant_id = second_candidate.tenant_id
    deps.active_retrieval_run_id = second_candidate.run_id
    second = tool.function(context, "same question", ["media-1"])

    assert first["status"] == second["status"] == "ok"
    assert len(service.calls) == 2
    assert getattr(service.calls[1], changed_field) == changed_value
    assert len(deps.media_inspection_cache.values) == 2


@pytest.mark.parametrize(
    "updates",
    [
        {"media_id": "wrong-media"},
        {"tenant_id": TENANT_ID + 1},
        {"run_id": "other-run"},
        {"content_hash": "wrong-hash"},
        {"model_version": "wrong-model"},
        {"prompt_version": "wrong-prompt"},
        {"confidence": 1.1},
        {"evidence": tuple(f"item-{index}" for index in range(9))},
        {"evidence": ("",)},
        {"start_sec": 20.0, "end_sec": 10.0},
    ],
    ids=[
        "wrong-media",
        "wrong-tenant",
        "wrong-run",
        "wrong-hash",
        "wrong-model",
        "wrong-prompt",
        "confidence-out-of-range",
        "too-many-evidence-items",
        "empty-evidence-item",
        "invalid-time-range",
    ],
)
def test_inspect_media_drops_wrong_or_unbounded_results_without_evidence(updates):
    # A result that fails provenance or bounds validation is discarded for that
    # candidate: no evidence is published or cached, and the call itself stays
    # a successful, empty observation rather than a provider failure.
    def result_factory(request):
        return replace(_VisionService._result(request), **updates)

    service = _VisionService(result_factory=result_factory)
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate()), vision_service=service
    )

    result = tool.function(context, "inspect this", ["media-1"])

    assert result == {"status": "ok", "evidence": [], "reason": None}
    assert deps.media_evidence == {}
    assert deps.media_inspection_cache.values == {}
    assert [observation.outcome for observation in deps.tool_observations] == [
        "started",
        "succeeded",
    ]


def test_inspect_media_maps_provider_failures_without_leaking_details():
    def fail(_request):
        raise RuntimeError("provider-secret")

    service = _VisionService(result_factory=fail)
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate()), vision_service=service
    )

    result = tool.function(context, "inspect this", ["media-1"])

    assert result == {
        "status": "error",
        "error": {"category": "provider_failure", "code": "vision_failed"},
        "evidence": [],
    }
    assert "provider-secret" not in str(result)
    assert [observation.outcome for observation in deps.tool_observations] == [
        "started",
        "failed",
    ]


def test_inspect_media_uses_one_invocation_deadline_for_all_candidates(monkeypatch):
    started = threading.Event()

    def wait_long_enough(request):
        started.set()
        time.sleep(0.06 if request.media_id == "media-1" else 0.08)
        return _VisionService._result(request)

    monkeypatch.setattr(
        "app.agent.agent_tools.retrieval.INSPECT_MEDIA_TIMEOUT_SECONDS", 0.1
    )
    service = _VisionService(result_factory=wait_long_enough)
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate("media-1"), _candidate("media-2")),
        vision_service=service,
    )

    result = tool.function(context, "inspect this", ["media-1", "media-2"])

    assert started.is_set()
    assert result == {
        "status": "error",
        "error": {"category": "timeout", "code": "vision_timeout"},
        "evidence": [],
    }
    assert [observation.outcome for observation in deps.tool_observations] == [
        "started",
        "failed",
    ]


def test_inspect_media_has_a_dedicated_call_budget():
    service = _VisionService()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(_candidate()), vision_service=service
    )

    results = [
        tool.function(context, f"question-{index}", ["media-1"])
        for index in range(INSPECT_MEDIA_CALLS_LIMIT + 1)
    ]

    assert [result["status"] for result in results[:INSPECT_MEDIA_CALLS_LIMIT]] == [
        "ok"
    ] * INSPECT_MEDIA_CALLS_LIMIT
    assert results[-1] == {
        "status": "error",
        "error": {"category": "limit", "code": "vision_budget_exhausted"},
        "evidence": [],
    }
    assert deps.media_inspection_calls == INSPECT_MEDIA_CALLS_LIMIT
    assert len(service.calls) == INSPECT_MEDIA_CALLS_LIMIT


@pytest.mark.parametrize(
    ("manifest", "vision_service"),
    [
        (None, None),
        (_manifest(_candidate()), None),
        (MediaCandidateManifest(TENANT_ID, RUN_ID, ()), _VisionService()),
    ],
)
def test_inspect_media_is_hidden_when_feature_is_not_available(
    manifest, vision_service
):
    tool, context, deps = _tool_and_context(
        manifest=manifest, vision_service=vision_service
    )

    assert tool.prepare(context, tool.tool_def) is None
    assert deps.media_inspection_calls == 0


def test_inspect_media_remains_registered_alongside_existing_retrieval_tools():
    agent = build_agent("test")

    assert {"inspect_media", "search_segments"} <= set(agent._function_toolset.tools)


def test_inspect_media_is_hidden_when_rollout_flag_is_off_even_with_dependencies():
    candidate = _candidate()
    tool, context, deps = _tool_and_context(
        manifest=_manifest(candidate),
        vision_service=_VisionService(),
        multimodal_retrieval_enabled=False,
    )

    assert tool.prepare(context, tool.tool_def) is None
    assert deps.media_inspection_calls == 0
