from __future__ import annotations

import builtins
import math
import sys
import types
from contextlib import contextmanager

import pytest

import app.ingest.wemm_local as wemm_local
from app.bootstrap import build_multimodal_embedding_provider
from app.config import Settings
from app.ingest.embed import (
    EmbeddingError,
    EmbeddingInputError,
    MediaObjectReference,
    MultimodalInput,
)
from app.ingest.wemm_local import (
    DEFAULT_WEMM_DIMENSIONS,
    DEFAULT_WEMM_MODEL,
    DEFAULT_WEMM_REVISION,
    WEMM_LOCAL_PROTOCOL_VERSION,
    WemmLocalEmbedder,
)


SUPPORTED_DIMENSIONS = (64, 128, 256, 512, 1024, 2048)


class _FakeConfig:
    def __init__(self, dimensions):
        self.matryoshka_dimensions = dimensions


class _FakeComponent:
    def __init__(self, dimensions):
        self.auto_model = types.SimpleNamespace(config=_FakeConfig(dimensions))


class _FakeModel:
    def __init__(self, dimensions=64, *, supported=SUPPORTED_DIMENSIONS, output=None, error=None):
        self.dimensions = dimensions
        self.calls: list[tuple[list[object], dict]] = []
        self.component = _FakeComponent(supported)
        self.output = output
        self.error = error

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.component

    def encode(self, inputs, **kwargs):
        self.calls.append((list(inputs), kwargs))
        if self.error is not None:
            raise self.error
        if self.output is not None:
            return self.output(list(inputs)) if callable(self.output) else self.output
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in inputs]


class _RecordingLoader:
    def __init__(self, model):
        self.model = model
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model, revision, device):
        self.calls.append((model, revision, device))
        return self.model


class _RecordingResolver:
    def __init__(self, path, *, error=None):
        self.path = path
        self.error = error
        self.references: list[MediaObjectReference] = []
        self.events: list[tuple[str, str]] = []

    def resolve(self, reference):
        self.references.append(reference)
        if self.error is not None:
            raise self.error

        @contextmanager
        def managed_path():
            self.events.append(("enter", reference.object_id))
            try:
                yield self.path
            finally:
                self.events.append(("exit", reference.object_id))

        return managed_path()


def _text(value: str) -> MultimodalInput:
    return MultimodalInput("text", value)


def _media(object_id: str, modality: str = "image", content_hash: str | None = "hash"):
    return MultimodalInput(
        modality,
        MediaObjectReference(object_id, modality, content_hash),
    )


def _row(*values: float, dimensions: int = 64) -> list[float]:
    return [*values, *([0.0] * (dimensions - len(values)))]


def test_constructor_is_lazy_and_does_not_import_or_construct_runtime(monkeypatch):
    imported: list[str] = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            imported.append(name)
            raise AssertionError("SentenceTransformer must be imported lazily")
        return real_import(name, *args, **kwargs)

    loader = _RecordingLoader(_FakeModel())
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = WemmLocalEmbedder(model_loader=loader)

    assert imported == []
    assert loader.calls == []
    assert provider.model == DEFAULT_WEMM_MODEL
    assert provider.revision == DEFAULT_WEMM_REVISION
    assert provider.dimensions == DEFAULT_WEMM_DIMENSIONS
    assert provider.metadata.protocol_version == WEMM_LOCAL_PROTOCOL_VERSION


def test_default_loader_imports_sentence_transformers_only_when_called(monkeypatch):
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    model = wemm_local._load_sentence_transformer(
        "local/model",
        "a" * 40,
        "cuda",
    )

    assert isinstance(model, FakeSentenceTransformer)
    assert calls == [
        (
            ("local/model",),
            {
                "revision": "a" * 40,
                "trust_remote_code": True,
                "device": "cuda",
            },
        )
    ]


def test_pinned_model_revision_and_device_reach_loader_on_first_encode():
    model = _FakeModel(dimensions=64)
    loader = _RecordingLoader(model)
    provider = WemmLocalEmbedder(
        model="org/wemm-test",
        revision="b" * 40,
        dimensions=64,
        device="cuda:1",
        model_loader=loader,
    )

    assert loader.calls == []
    assert provider.embed(["query"])[0][:2] == [1.0, 0.0]
    assert loader.calls == [("org/wemm-test", "b" * 40, "cuda:1")]


def test_encode_receives_exact_text_image_and_video_inputs_and_kwargs(tmp_path):
    image_path = tmp_path / "image.bin"
    video_path = tmp_path / "video.bin"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
    image_ref = MediaObjectReference("opaque-image", "image", "image-hash")
    video_ref = MediaObjectReference("opaque-video", "video", "video-hash")
    class _ImageThenVideoResolver(_RecordingResolver):
        def resolve(self, reference):
            self.references.append(reference)
            path = image_path if reference.modality == "image" else video_path

            @contextmanager
            def managed_path():
                self.events.append(("enter", reference.object_id))
                try:
                    yield str(path)
                finally:
                    self.events.append(("exit", reference.object_id))

            return managed_path()

    resolver = _ImageThenVideoResolver(str(image_path))
    model = _FakeModel(dimensions=64)
    provider = WemmLocalEmbedder(
        dimensions=64,
        batch_size=3,
        media_resolver=resolver,
        model_loader=_RecordingLoader(model),
    )

    result = provider.embed_multimodal(
        [
            _text("caption"),
            MultimodalInput("image", image_ref),
            MultimodalInput("video", video_ref),
        ]
    )

    assert len(result) == 3
    assert model.calls == [
        (
            [
                "caption",
                {"image": str(image_path)},
                {"video": str(video_path)},
            ],
            {
                "batch_size": 3,
                "truncate_dim": 64,
                "normalize_embeddings": True,
                "convert_to_tensor": True,
            },
        )
    ]
    assert resolver.references == [image_ref, video_ref]
    assert resolver.events == [
        ("enter", "opaque-image"),
        ("enter", "opaque-video"),
        ("exit", "opaque-video"),
        ("exit", "opaque-image"),
    ]
    assert "opaque-image" not in repr(model.calls[0][0])
    assert "opaque-video" not in repr(model.calls[0][0])


@pytest.mark.parametrize("dimensions", SUPPORTED_DIMENSIONS)
def test_supported_matryoshka_dimensions_are_accepted(dimensions):
    model = _FakeModel(dimensions=dimensions)
    provider = WemmLocalEmbedder(
        dimensions=dimensions,
        model_loader=_RecordingLoader(model),
    )

    result = provider.embed(["dimension-check"])[0]

    assert len(result) == dimensions
    assert math.isclose(math.sqrt(math.fsum(value * value for value in result)), 1.0)


def test_default_dimension_is_1024():
    assert WemmLocalEmbedder().dimensions == 1024


def test_batching_preserves_input_order():
    def output(inputs):
        rows = []
        for value in inputs:
            index = int(value.rsplit("-", 1)[1])
            rows.append(_row(float(index + 1), float(10 - index)))
        return rows

    model = _FakeModel(dimensions=64, output=output)
    provider = WemmLocalEmbedder(
        dimensions=64,
        batch_size=2,
        model_loader=_RecordingLoader(model),
    )

    result = provider.embed(["item-0", "item-1", "item-2"])

    assert [inputs for inputs, _kwargs in model.calls] == [
        ["item-0", "item-1"],
        ["item-2"],
    ]
    assert result[0][:2] == pytest.approx(
        [1 / math.sqrt(101), 10 / math.sqrt(101)]
    )
    assert result[1][:2] == pytest.approx(
        [2 / math.sqrt(85), 9 / math.sqrt(85)]
    )
    assert result[2][:2] == pytest.approx(
        [3 / math.sqrt(73), 8 / math.sqrt(73)]
    )
    assert all(math.isfinite(value) for row in result for value in row)


@pytest.mark.parametrize(
    "item",
    [
        MultimodalInput("image", "https://media.example/image.jpg"),
        MultimodalInput("image", "/private/media.jpg"),
        MultimodalInput("image", b"raw-content"),
        MultimodalInput(
            "image", MediaObjectReference("https://media.example/image.jpg", "image")
        ),
        MultimodalInput("image", MediaObjectReference("/private/media.jpg", "image")),
        MultimodalInput("image", MediaObjectReference("object id", "image")),
        MultimodalInput(
            "image", MediaObjectReference("object-1", "image", "hash value")
        ),
        MultimodalInput(
            "image", MediaObjectReference("object-1", "image", "h" * 129)
        ),
        MultimodalInput(
            "image", MediaObjectReference("o" * 513, "image")
        ),
    ],
)
def test_media_inputs_reject_urls_paths_raw_content_and_invalid_opaque_bounds(item):
    with pytest.raises(EmbeddingInputError) as caught:
        WemmLocalEmbedder().embed_multimodal([item])

    assert caught.value.code == "invalid_input"


def test_media_requires_an_authorized_resolver():
    with pytest.raises(EmbeddingInputError, match="authorized media resolver") as caught:
        WemmLocalEmbedder().embed_multimodal([_media("opaque-image")])

    assert caught.value.code == "invalid_input"


def test_input_and_text_limits_are_checked_before_model_loading():
    loader = _RecordingLoader(_FakeModel(dimensions=64))
    provider = WemmLocalEmbedder(
        dimensions=64,
        max_inputs=2,
        max_text_chars=3,
        model_loader=loader,
    )

    with pytest.raises(EmbeddingInputError, match="input count exceeds limit") as count_error:
        provider.embed(["a", "b", "c"])
    assert count_error.value.code == "invalid_input"

    with pytest.raises(EmbeddingInputError, match="character limit") as text_error:
        provider.embed(["four"])
    assert text_error.value.code == "invalid_input"
    assert loader.calls == []


def test_media_file_size_limit_is_enforced_before_encode(tmp_path):
    path = tmp_path / "too-large.bin"
    path.write_bytes(b"1234")
    resolver = _RecordingResolver(str(path))
    model = _FakeModel(dimensions=64)
    provider = WemmLocalEmbedder(
        dimensions=64,
        max_media_bytes=3,
        media_resolver=resolver,
        model_loader=_RecordingLoader(model),
    )

    with pytest.raises(EmbeddingError) as caught:
        provider.embed_multimodal([_media("opaque-image")])

    assert caught.value.code == "wemm_media_resolution_failed"
    assert model.calls == []


def test_media_file_must_be_a_non_empty_regular_file(tmp_path):
    path = tmp_path / "empty.bin"
    path.touch()
    provider = WemmLocalEmbedder(
        dimensions=64,
        media_resolver=_RecordingResolver(str(path)),
        model_loader=_RecordingLoader(_FakeModel(dimensions=64)),
    )

    with pytest.raises(EmbeddingError) as caught:
        provider.embed_multimodal([_media("opaque-image")])

    assert caught.value.code == "wemm_media_resolution_failed"


@pytest.mark.parametrize(
    "resolved_path",
    [
        "relative-media.bin",
        "https://media.example/media.bin",
        b"raw-media",
    ],
)
def test_resolver_must_return_an_absolute_local_path(resolved_path):
    model = _FakeModel(dimensions=64)
    provider = WemmLocalEmbedder(
        dimensions=64,
        media_resolver=_RecordingResolver(resolved_path),
        model_loader=_RecordingLoader(model),
    )

    with pytest.raises(EmbeddingError) as caught:
        provider.embed_multimodal([_media("opaque-image")])

    assert caught.value.code == "wemm_media_resolution_failed"
    assert model.calls == []


def test_media_file_resolver_errors_are_stable_and_redacted(tmp_path):
    path = tmp_path / "media.bin"
    path.write_bytes(b"media")
    model = _FakeModel(dimensions=64)
    provider = WemmLocalEmbedder(
        dimensions=64,
        media_resolver=_RecordingResolver(
            str(path), error=RuntimeError("secret resolver details")
        ),
        model_loader=_RecordingLoader(model),
    )

    with pytest.raises(EmbeddingError) as caught:
        provider.embed_multimodal([_media("opaque-image")])

    assert caught.value.code == "wemm_media_resolution_failed"
    assert str(caught.value) == "authorized media resolution failed"
    assert "secret resolver details" not in str(caught.value)


@pytest.mark.parametrize(
    ("raw_output", "error_code"),
    [
        ([_row(3.0, 4.0)], None),
        ([_row(float("nan"), 1.0)], "wemm_invalid_output"),
        ([_row(float("inf"), 1.0)], "wemm_invalid_output"),
        ([_row(0.0, 0.0)], "wemm_zero_vector"),
        ([[1.0]], "wemm_invalid_output"),
    ],
)
def test_outputs_are_finite_normalized_or_fail_with_stable_code(raw_output, error_code):
    model = _FakeModel(dimensions=64, output=raw_output)
    provider = WemmLocalEmbedder(
        dimensions=64,
        model_loader=_RecordingLoader(model),
    )

    if error_code is None:
        result = provider.embed(["normalized"])[0]
        assert result[:2] == pytest.approx([0.6, 0.8])
        assert all(math.isfinite(value) for value in result)
        assert math.isclose(math.sqrt(math.fsum(value * value for value in result)), 1.0)
    else:
        with pytest.raises(EmbeddingError) as caught:
            provider.embed(["invalid"])
        assert caught.value.code == error_code


def test_model_load_and_encode_errors_are_stable_and_redacted():
    def failing_loader(*_args):
        raise RuntimeError("secret download location")

    with pytest.raises(EmbeddingError) as load_error:
        WemmLocalEmbedder(model_loader=failing_loader).embed(["load"])
    assert load_error.value.code == "wemm_model_load_failed"
    assert str(load_error.value) == "WeMM model load failed"
    assert "secret download location" not in str(load_error.value)

    model = _FakeModel(error=RuntimeError("secret model output"))
    with pytest.raises(EmbeddingError) as encode_error:
        WemmLocalEmbedder(model_loader=_RecordingLoader(model)).embed(["encode"])
    assert encode_error.value.code == "wemm_encode_failed"
    assert str(encode_error.value) == "WeMM model encode failed"
    assert "secret model output" not in str(encode_error.value)


@pytest.mark.parametrize(
    ("supported", "dimensions", "error_code"),
    [
        (None, 64, "wemm_dimensions_unavailable"),
        ((64, 128), 256, "wemm_dimension_unsupported"),
    ],
)
def test_model_dimension_metadata_failures_are_stable(supported, dimensions, error_code):
    model = _FakeModel(dimensions=dimensions, supported=supported)
    provider = WemmLocalEmbedder(
        dimensions=dimensions,
        model_loader=_RecordingLoader(model),
    )

    with pytest.raises(EmbeddingError) as caught:
        provider.embed(["dimension"])

    assert caught.value.code == error_code


def test_bootstrap_composes_pinned_local_provider_without_loading_model():
    model = _FakeModel(dimensions=64)
    loader = _RecordingLoader(model)
    settings = Settings(
        multimodal_retrieval_enabled=True,
        wemm_enabled=True,
        wemm_model="org/wemm-test",
        wemm_revision="c" * 40,
        wemm_dimensions=64,
        wemm_device="cpu",
        wemm_batch_size=2,
        wemm_max_inputs=4,
        wemm_max_text_chars=100,
        wemm_max_media_bytes=1024,
    )

    provider = build_multimodal_embedding_provider(settings, model_loader=loader)

    assert isinstance(provider, WemmLocalEmbedder)
    assert provider.metadata.model == "org/wemm-test"
    assert provider.metadata.revision == "c" * 40
    assert provider.metadata.dimensions == 64
    assert provider.device == "cpu"
    assert provider.batch_size == 2
    assert provider.max_inputs == 4
    assert provider.max_text_chars == 100
    assert provider.max_media_bytes == 1024
    assert loader.calls == []
