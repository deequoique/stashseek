from __future__ import annotations

import math
from dataclasses import replace

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.agent.media import MediaCandidate, MediaCandidateManifest
from app.agent.runtime import AgentDeps, build_agent
from app.agent.types import (
    Citation,
    UnifiedRetrievalCandidate,
    UnifiedRetrievalResult,
)
from app.bootstrap import build_multimodal_embedding_provider
from app.config import Settings
from app.ingest.wemm_local import WemmLocalEmbedder


class _FakeWemmModel:
    def __init__(self, dimensions: int):
        self.calls: list[tuple[list[object], dict]] = []
        self._component = type(
            "Component",
            (),
            {
                "auto_model": type(
                    "AutoModel",
                    (),
                    {
                        "config": type(
                            "Config",
                            (),
                            {
                                "matryoshka_dimensions": (
                                    64,
                                    128,
                                    256,
                                    512,
                                    1024,
                                    2048,
                                )
                            },
                        )()
                    },
                )()
            },
        )()
        self.dimensions = dimensions

    def __getitem__(self, index: int):
        if index != 0:
            raise IndexError(index)
        return self._component

    def encode(self, inputs, **kwargs):
        self.calls.append((list(inputs), kwargs))
        rows = []
        for text in inputs:
            index = int(str(text).rsplit("-", 1)[1])
            row = [float(index + 1), float(1000 - index)]
            row.extend([0.0] * (self.dimensions - len(row)))
            rows.append(row)
        return rows


class _Loader:
    def __init__(self, dimensions: int):
        self.dimensions = dimensions
        self.calls: list[tuple[str, str, str]] = []
        self.model = _FakeWemmModel(dimensions)

    def __call__(self, model: str, revision: str, device: str):
        self.calls.append((model, revision, device))
        return self.model


@pytest.mark.parametrize(
    ("multimodal_enabled", "wemm_enabled", "provider_expected"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
    ids=[
        "both-off",
        "wemm-only",
        "multimodal-only",
        "both-on",
    ],
)
def test_multimodal_provider_requires_both_feature_flags(
    multimodal_enabled, wemm_enabled, provider_expected
):
    loader = _Loader(64)
    settings = replace(
        Settings(),
        multimodal_retrieval_enabled=multimodal_enabled,
        wemm_enabled=wemm_enabled,
        wemm_dimensions=64,
    )

    provider = build_multimodal_embedding_provider(
        settings,
        model_loader=loader,
    )

    assert (provider is not None) is provider_expected
    if provider_expected:
        assert isinstance(provider, WemmLocalEmbedder)
        assert provider.metadata.model == settings.wemm_model
        assert provider.metadata.revision == settings.wemm_revision
        assert provider.metadata.dimensions == 64
    assert loader.calls == []


def test_wemm_embed_batches_arbitrary_text_and_restores_input_order():
    loader = _Loader(64)
    embedder = WemmLocalEmbedder(
        dimensions=64,
        batch_size=8,
        model_loader=loader,
    )
    texts = [f"arbitrary-text-{index}" for index in range(17)]

    embeddings = embedder.embed(texts)

    assert [len(inputs) for inputs, _kwargs in loader.model.calls] == [8, 8, 1]
    assert [
        text for inputs, _kwargs in loader.model.calls for text in inputs
    ] == texts
    assert len(embeddings) == len(texts)
    for index, embedding in enumerate(embeddings):
        norm = math.sqrt((index + 1) ** 2 + (1000 - index) ** 2)
        assert embedding[:2] == pytest.approx(
            [(index + 1) / norm, (1000 - index) / norm]
        )
    assert loader.calls == [
        (Settings().wemm_model, Settings().wemm_revision, Settings().wemm_device)
    ]


class _SearchServices:
    def __init__(self, citations: list[Citation]):
        self.citations = citations
        self.calls: list[tuple[str, int, int | None]] = []

    def search_segments(self, query, *, limit=10, item_id=None):
        self.calls.append((query, limit, item_id))
        return self.citations


def test_search_segments_keeps_legacy_payload_and_populates_unified_result():
    citation = Citation(
        item_id=11,
        segment_id=42,
        title="Source",
        excerpt="Evidence",
        url="https://example.test/source?t=12",
        start_sec=12.0,
    )
    citation._retrieval_score = 0.75
    services = _SearchServices([citation])
    deps = AgentDeps(services=services, actions=object())
    context = RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
    )
    tool = build_agent("test")._function_toolset.tools["search_segments"]

    payload = tool.function(context, "find the source")

    assert payload == {
        "status": "ok",
        "evidence": [citation.model_dump()],
        "reason": None,
    }
    assert services.calls == [("find the source", 10, None)]
    assert deps.last_retrieval_result == UnifiedRetrievalResult(
        candidates=(
            UnifiedRetrievalCandidate(
                candidate_id="segment:42",
                kind="text",
                score=0.75,
                item_id=11,
                segment_id=42,
                start_sec=12.0,
            ),
        ),
        source="text",
    )


def test_inspect_media_is_hidden_when_rollout_flag_is_off_even_with_manifest():
    candidate = MediaCandidate(
        media_id="media-1",
        item_id=7,
        tenant_id=7,
        run_id="run-1",
        content_hash="hash-1",
        model_version="vision-v1",
        prompt_version="prompt-v1",
    )
    deps = AgentDeps(
        services=object(),
        actions=object(),
        media_manifest=MediaCandidateManifest(7, "run-1", (candidate,)),
        vision_service=object(),
        active_tenant_id=7,
        active_retrieval_run_id="run-1",
        multimodal_retrieval_enabled=False,
    )
    context = RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
    )
    tool = build_agent("test")._function_toolset.tools["inspect_media"]

    assert tool.prepare(context, tool.tool_def) is None
    assert deps.media_inspection_calls == 0
