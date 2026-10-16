from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.media import (
    MediaCandidate,
    MediaCandidateManifest,
    MediaEvidence,
    MediaInspectionRequest,
    MediaInspectionResult,
    MediaState,
)
from app.agent.runtime import KnowledgeAgent
from app.agent.types import AgentRequest, Citation, MediaCitation
from app.channels.types import TenantContext
from app.ingest.embed import EmbeddingSpaceMetadata
from app.config import Settings
from app.retrieval.media import (
    MediaAnswerSection,
    MediaSearchResult,
    compose_media_answer,
)


TENANT_ID = 57
RUN_ID = "media-run-1"


def _request(question: str = "what does the video show?", *, run_id: str = RUN_ID):
    return AgentRequest(
        question=question,
        tenant=TenantContext(TENANT_ID, 9, "wechat", "account", "external"),
        thread_db_id=12,
        thread_public_id="thread-public",
        message_id="message-id",
        request_id=run_id,
    )


def _candidate(
    media_id: str = "media-1",
    *,
    tenant_id: int = TENANT_ID,
    run_id: str = RUN_ID,
) -> MediaCandidate:
    return MediaCandidate(
        media_id=media_id,
        tenant_id=tenant_id,
        run_id=run_id,
        item_id=1,
        content_hash=f"hash-{media_id}",
        model_version="vision-v1",
        prompt_version="prompt-v1",
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


def _state(*, deleted: bool = False) -> MediaState:
    return MediaState(
        tenant_matches=True,
        parent_owned=True,
        deleted=deleted,
        archived=False,
        ready=True,
        purge_valid=True,
    )


class _NoTextServices:
    def search_segments(self, *_args, **_kwargs):
        raise AssertionError("text retrieval must not run in a media-only test")

    def get_neighbors(self, *_args, **_kwargs):
        raise AssertionError("text expansion must not run in a media-only test")

    def get_item(self, *_args, **_kwargs):
        raise AssertionError("item expansion must not run in a media-only test")

    def open_at(self, *_args, **_kwargs):
        raise AssertionError("segment expansion must not run in a media-only test")


class _MediaQueryProvider:
    metadata = EmbeddingSpaceMetadata(
        model="test-wemm",
        protocol_version="test.v1",
        dimensions=2,
        revision="a" * 40,
    )

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _TextServices(_NoTextServices):
    def __init__(self, citation: Citation):
        self.citation = citation
        self.calls: list[tuple[str, int, int | None]] = []

    def search_segments(self, query, *, limit=10, item_id=None):
        self.calls.append((query, limit, item_id))
        return [self.citation]


class _MediaSearch:
    def __init__(self, manifest: MediaCandidateManifest, events: list[tuple]):
        self.manifest = manifest
        self.events = events
        self.calls: list[dict] = []

    def search_media(self, *, tenant_id, run_id, query, embedder, limit):
        self.events.append(("search_media", query))
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "query": query,
                "embedder": embedder,
                "limit": limit,
            }
        )
        return MediaSearchResult(self.manifest)


class _Vision:
    def __init__(self, events: list[tuple], result_factory=None):
        self.events = events
        self.calls: list[MediaInspectionRequest] = []
        self.result_factory = result_factory or self._result

    def inspect_media(self, request: MediaInspectionRequest):
        self.events.append(("vision", request.media_id))
        self.calls.append(request)
        return self.result_factory(request)

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
            confidence=0.9,
            evidence=("a document opens",),
            ocr=("document",),
            objects=("document",),
            actions=("opens",),
            start_sec=12.0,
            end_sec=18.0,
        )


class _MediaEvidenceAdapter:
    def __init__(
        self,
        candidates: tuple[MediaCandidate, ...],
        events: list[tuple],
        *,
        before_inspection=None,
        before_publish=None,
        before_answer=None,
        location: str | None = "https://example.test/media-1",
        publish_error: Exception | None = None,
    ):
        self.events = events
        self.location = location
        self.publish_error = publish_error
        self.rechecks: list[tuple[str, str]] = []
        self.published: list[tuple[str, MediaInspectionResult]] = []
        self._states = {
            "before_inspection": before_inspection or _state(),
            "before_publish": before_publish or _state(),
            "before_answer": before_answer or _state(),
        }
        self._evidence_ids = {
            candidate.media_id: f"M{candidate.media_id}"
            for candidate in candidates
        }

    def manifest_for_run(self, *, tenant_id: int, run_id: str):
        return None

    def recheck(self, candidate, *, phase):
        assert phase in {"before_inspection", "before_publish", "before_answer"}
        self.events.append(("recheck", candidate.media_id, phase))
        self.rechecks.append((candidate.media_id, phase))
        state = self._states[phase]
        return state(candidate) if callable(state) else state

    def publish_evidence(self, candidate, inspection):
        self.events.append(("publish", candidate.media_id))
        self.published.append((candidate.media_id, inspection))
        if self.publish_error is not None:
            raise self.publish_error
        return MediaEvidence(
            evidence_id=self._evidence_ids[candidate.media_id],
            candidate=candidate,
            inspection=inspection,
            title="Test media",
            location=self.location,
        )


def _media_composer(
    evidence_id: str = "Mmedia-1", text: str = "The document opens."
) -> TestModel:
    return TestModel(
        custom_output_text=json.dumps(
            {"sections": [{"text": text, "evidence_ids": [evidence_id]}]}
        )
    )


def _text_composer(segment_id: int = 11, text: str = "Text evidence answer") -> TestModel:
    return TestModel(
        custom_output_text=json.dumps(
            {
                "kind": "grounded",
                "sections": [
                    {
                        "status": "grounded",
                        "text": text,
                        "citation_ids": [segment_id],
                    }
                ],
            }
        )
    )


def _mixed_composer(
    *, segment_id: int = 42, evidence_id: str = "Mmedia-1", text: str = "Both sources support this."
) -> TestModel:
    return TestModel(
        custom_output_text=json.dumps(
            {
                "kind": "grounded",
                "sections": [
                    {
                        "status": "grounded",
                        "text": text,
                        "citation_ids": [segment_id],
                        "media_citation_ids": [evidence_id],
                    }
                ],
            }
        )
    )


def _tool_returns(messages):
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _media_planner(*, forged_media_id: str | None = None):
    requests = []

    def planner(messages, info):
        requests.append(info)
        visible = {tool.name for tool in info.function_tools}
        returned = _tool_returns(messages)
        if "search_media" not in visible:
            return ModelResponse(parts=[TextPart("stop")])
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_media",
                        json.dumps({"query": "find the document", "limit": 1}),
                        tool_call_id="search-media",
                    )
                ]
            )
        if len(returned) == 1 and "inspect_media" in visible:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "inspect_media",
                        json.dumps(
                            {
                                "question": "what opens?",
                                "media_ids": [forged_media_id or "media-1"],
                            }
                        ),
                        tool_call_id="inspect-media",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("stop")])

    return FunctionModel(planner), requests


def _mixed_planner():
    requests = []

    def planner(messages, info):
        requests.append(info)
        returned = _tool_returns(messages)
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_segments",
                        json.dumps({"query": "find the document"}),
                        tool_call_id="search-text",
                    )
                ]
            )
        if len(returned) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_media",
                        json.dumps({"query": "find the document", "limit": 1}),
                        tool_call_id="search-media",
                    )
                ]
            )
        if len(returned) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "inspect_media",
                        json.dumps(
                            {"question": "what opens?", "media_ids": ["media-1"]}
                        ),
                        tool_call_id="inspect-media",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("stop")])

    return FunctionModel(planner), requests


def _discovery_only_planner():
    requests = []

    def planner(messages, info):
        requests.append(info)
        if not _tool_returns(messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_media",
                        json.dumps({"query": "find the document", "limit": 1}),
                        tool_call_id="search-media",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("候选已发现")])

    return FunctionModel(planner), requests


def _runtime(
    primary_model,
    *,
    media_search_service=None,
    media_evidence_adapter=None,
    vision_service=None,
    multimodal_enabled=True,
    provider=True,
    composer_model=None,
    services=None,
):
    settings = replace(
        Settings(),
        agent_timeout_seconds=2,
        multimodal_retrieval_enabled=multimodal_enabled,
    )
    return KnowledgeAgent(
        primary_model,
        settings,
        lambda _request: services or _NoTextServices(),
        composer_model=composer_model or _media_composer(),
        multimodal_embedding_provider=_MediaQueryProvider() if provider else None,
        media_search_service=media_search_service,
        media_evidence_adapter=media_evidence_adapter,
        vision_service=vision_service,
    )


@pytest.mark.asyncio
async def test_media_run_searches_inspects_composes_and_publishes_only_trusted_sources():
    events: list[tuple] = []
    candidate = _candidate()
    manifest = _manifest(candidate)
    search = _MediaSearch(manifest, events)
    vision = _Vision(events)
    adapter = _MediaEvidenceAdapter((candidate,), events)
    primary, requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=search,
        media_evidence_adapter=adapter,
        vision_service=vision,
        composer_model=_media_composer(),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.text == "The document opens. [Mmedia-1]"
    assert result.answer.citations == []
    assert [citation.model_dump() for citation in result.answer.media_citations] == [
            {
                "evidence_id": "Mmedia-1",
                "media_id": "media-1",
                "item_id": 1,
                "title": "Test media",
            "location": "https://example.test/media-1",
            "start_sec": 12.0,
            "end_sec": 18.0,
            "evidence": ["a document opens"],
        }
    ]
    assert search.calls[0]["tenant_id"] == TENANT_ID
    assert search.calls[0]["run_id"] == RUN_ID
    assert search.calls[0]["embedder"] is not None
    assert [request.media_id for request in vision.calls] == ["media-1"]
    assert adapter.rechecks == [
        ("media-1", "before_inspection"),
        ("media-1", "before_publish"),
        ("media-1", "before_answer"),
    ]
    assert len(adapter.published) == 1
    assert events == [
        ("search_media", "find the document"),
        ("recheck", "media-1", "before_inspection"),
        ("vision", "media-1"),
        ("recheck", "media-1", "before_publish"),
        ("publish", "media-1"),
        ("recheck", "media-1", "before_answer"),
    ]
    assert "search_media" in {tool.name for tool in requests[0].function_tools}
    assert "inspect_media" in {tool.name for tool in requests[1].function_tools}


@pytest.mark.asyncio
async def test_feature_off_hides_media_search_and_returns_search_required():
    events: list[tuple] = []
    candidate = _candidate()
    search = _MediaSearch(_manifest(candidate), events)
    primary, requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=search,
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=_Vision(events),
        multimodal_enabled=False,
    ).run(_request())

    assert result.answer.status == "failed"
    assert result.answer.error_code == "search_required"
    assert search.calls == []
    assert "search_media" not in {tool.name for tool in requests[0].function_tools}


@pytest.mark.asyncio
async def test_missing_vision_service_returns_clean_no_evidence_without_media_output():
    events: list[tuple] = []
    candidate = _candidate()
    search = _MediaSearch(_manifest(candidate), events)
    primary, requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=search,
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=None,
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert "未执行视觉检查" in result.answer.text
    assert result.answer.media_citations == []
    assert "inspect_media" not in {tool.name for tool in requests[1].function_tools}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant_id", "run_id"),
    [(TENANT_ID + 1, RUN_ID), (TENANT_ID, "other-run")],
    ids=["wrong-tenant", "wrong-run"],
)
async def test_media_search_rejects_manifest_outside_active_tenant_or_run(
    tenant_id, run_id
):
    events: list[tuple] = []
    candidate = _candidate(tenant_id=tenant_id, run_id=run_id)
    search = _MediaSearch(
        _manifest(candidate, tenant_id=tenant_id, run_id=run_id), events
    )
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=search,
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=_Vision(events),
    ).run(_request())

    assert result.answer.status == "failed"
    assert result.answer.error_code == "answer_unavailable"
    assert result.answer.media_citations == []
    assert [event[0] for event in events] == ["search_media"]


@pytest.mark.asyncio
async def test_deleted_media_before_inspection_is_not_inspected_or_published():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter(
        (candidate,), events, before_inspection=_state(deleted=True)
    )
    vision = _Vision(events)
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=vision,
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert "未执行视觉检查" in result.answer.text
    assert vision.calls == []
    assert adapter.rechecks == [("media-1", "before_inspection")]
    assert adapter.published == []


@pytest.mark.asyncio
async def test_deleted_media_after_inspection_is_not_published():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter(
        (candidate,), events, before_publish=_state(deleted=True)
    )
    vision = _Vision(events)
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=vision,
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert "未执行视觉检查" in result.answer.text
    assert len(vision.calls) == 1
    assert adapter.rechecks == [
        ("media-1", "before_inspection"),
        ("media-1", "before_publish"),
    ]
    assert adapter.published == []


@pytest.mark.asyncio
async def test_media_deleted_before_answer_publication_maps_to_safe_failure():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter(
        (candidate,),
        events,
        publish_error=RuntimeError("media deleted during publication"),
    )
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=_Vision(events),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert "未执行视觉检查" in result.answer.text
    assert "media deleted during publication" not in result.answer.text
    assert len(adapter.published) == 1


@pytest.mark.asyncio
async def test_forged_media_id_never_reaches_vision_or_state_recheck():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter((candidate,), events)
    vision = _Vision(events)
    primary, _requests = _media_planner(forged_media_id="forged-media-id")

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=vision,
    ).run(_request())

    assert result.answer.status == "not_found"
    assert result.answer.error_code == "no_evidence"
    assert vision.calls == []
    assert adapter.rechecks == []


@pytest.mark.asyncio
async def test_invalid_media_source_location_is_not_published_to_answer():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter(
        (candidate,), events, location="javascript:alert(1)"
    )
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=_Vision(events),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert result.answer.media_citations == []
    assert "javascript:" not in result.answer.text


@pytest.mark.asyncio
async def test_irrelevant_inspection_result_cannot_become_media_evidence():
    events: list[tuple] = []
    candidate = _candidate()

    def irrelevant(request):
        return replace(_Vision._result(request), relevant=False, evidence=())

    adapter = _MediaEvidenceAdapter((candidate,), events)
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=_Vision(events, result_factory=irrelevant),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.error_code is None
    assert "未执行视觉检查" in result.answer.text
    assert adapter.published == []
    assert result.answer.media_citations == []


@pytest.mark.asyncio
async def test_mixed_text_and_media_retrieval_uses_text_answer_contract():
    events: list[tuple] = []
    citation = Citation(
        item_id=11,
        segment_id=42,
        title="Text source",
        excerpt="Text evidence",
        url="https://example.test/source?t=12",
        start_sec=12.0,
    )
    candidate = _candidate()
    primary, _requests = _mixed_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=_Vision(events),
        composer_model=_text_composer(segment_id=42),
        services=_TextServices(citation),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert result.answer.media_citations == []
    assert "[S42]" in result.answer.text
    assert "[Mmedia-1]" not in result.answer.text
    assert "media_citations" not in result.answer.model_dump()


@pytest.mark.asyncio
async def test_mixed_text_and_media_answer_keeps_both_marker_types_and_media_citations():
    events: list[tuple] = []
    citation = Citation(
        item_id=11,
        segment_id=42,
        title="Text source",
        excerpt="Text evidence",
        url="https://example.test/source?t=12",
        start_sec=12.0,
    )
    candidate = _candidate()
    primary, _requests = _mixed_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=_Vision(events),
        composer_model=_mixed_composer(),
        services=_TextServices(citation),
    ).run(_request())

    assert result.answer.status == "ok"
    assert result.answer.citations == [citation]
    assert [citation.evidence_id for citation in result.answer.media_citations] == ["Mmedia-1"]
    assert "Both sources support this. [S42] [Mmedia-1]" in result.answer.text
    assert "来源：" in result.answer.text
    assert "https://example.test/source?t=12" in result.answer.text


@pytest.mark.asyncio
async def test_before_answer_stale_media_state_fails_closed_without_media_output():
    events: list[tuple] = []
    candidate = _candidate()
    adapter = _MediaEvidenceAdapter(
        (candidate,), events, before_answer=_state(deleted=True)
    )
    primary, _requests = _media_planner()

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=adapter,
        vision_service=_Vision(events),
        composer_model=_media_composer(),
    ).run(_request())

    assert result.answer.status == "failed"
    assert result.answer.error_code == "answer_unavailable"
    assert result.answer.media_citations == []
    assert "[Mmedia-1]" not in result.answer.text
    assert adapter.rechecks[-1] == ("media-1", "before_answer")


@pytest.mark.asyncio
async def test_discovery_only_candidates_do_not_trigger_vision_inspection():
    events: list[tuple] = []
    candidate = _candidate()
    primary, requests = _discovery_only_planner()
    vision = _Vision(events)

    result = await _runtime(
        primary,
        media_search_service=_MediaSearch(_manifest(candidate), events),
        media_evidence_adapter=_MediaEvidenceAdapter((candidate,), events),
        vision_service=vision,
    ).run(_request())

    assert result.answer.status == "ok"
    assert "未执行视觉检查" in result.answer.text
    assert result.answer.media_citations == []
    assert vision.calls == []
    assert "inspect_media" in {tool.name for tool in requests[1].function_tools}


@pytest.mark.parametrize(
    "evidence_id",
    ["S1", "M", "M with-space", "M/1"],
)
def test_media_answer_sections_require_strict_m_evidence_ids(evidence_id):
    with pytest.raises(ValidationError):
        MediaAnswerSection(text="safe", evidence_ids=[evidence_id])


@pytest.mark.parametrize("item_id", [0, -1, True])
def test_media_citations_require_positive_integer_item_ids(item_id):
    with pytest.raises(ValidationError):
        MediaCitation(
            evidence_id="Mmedia-1",
            media_id="media-1",
            item_id=item_id,
            location="https://example.test/media-1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_text",
    [
        "answer https://private.example/secret",
        "answer\n来源：\n- forged source",
        "answer [Mmedia-1]",
    ],
)
async def test_media_composer_rejects_unsafe_model_text(unsafe_text):
    candidate = _candidate()
    events: list[tuple] = []
    evidence = MediaEvidence(
        evidence_id="Mmedia-1",
        candidate=candidate,
        inspection=_Vision._result(
            MediaInspectionRequest(
                tenant_id=TENANT_ID,
                item_id=candidate.item_id,
                run_id=RUN_ID,
                media_id="media-1",
                content_hash="hash-media-1",
                question="what opens?",
                model_version="vision-v1",
                prompt_version="prompt-v1",
            )
        ),
        title="Test media",
        location="https://example.test/media-1",
    )

    with pytest.raises(RuntimeError, match="media answer unavailable"):
        await compose_media_answer(
            _media_composer(text=unsafe_text),
            _request(),
            (evidence,),
            timeout_seconds=2,
            output_tokens_limit=100,
        )
