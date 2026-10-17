from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from app.ingest.embed import EmbeddingError, EmbeddingSpaceMetadata
from app.ingest.media import (
    InMemoryMediaEmbeddingStore,
    MediaEmbeddingIngestor,
    MediaIngestSource,
    MediaIngestionError,
    ObjectStoreMediaResolver,
    media_asset_id,
)


class _Provider:
    metadata = EmbeddingSpaceMetadata(
        model="tencent/WeMM-Embedding-2B",
        protocol_version="sentence-transformers-local.v1",
        dimensions=4,
        revision="a" * 40,
    )

    def __init__(self, *, failures: int = 0):
        self.failures = failures
        self.calls = []

    def embed_multimodal(self, inputs):
        self.calls.append(inputs)
        if self.failures:
            self.failures -= 1
            raise EmbeddingError("provider unavailable", code="wemm_encode_failed")
        return [[1.0, 0.0, 0.0, 0.0]]


class _ObjectStore:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get(self, key, *, max_bytes):
        self.calls.append((key, max_bytes))
        value = self.values[key]
        if len(value) > max_bytes:
            raise ValueError("too large")
        return value


def _image(**changes):
    source = MediaIngestSource(
        tenant_id=7,
        content_item_id=11,
        media_type="image",
        object_id="objects/image-1",
        sha256="A" * 64,
    )
    return replace(source, **changes)


def _video(**changes):
    source = MediaIngestSource(
        tenant_id=7,
        content_item_id=11,
        media_type="video",
        object_id="objects/video-1",
        sha256="b" * 64,
        video_start_sec=12,
        video_end_sec=24,
    )
    return replace(source, **changes)


def test_source_normalizes_hash_and_has_deterministic_slice_identity():
    first = _image()
    second = _image(sha256="a" * 64)

    assert first.normalized().sha256 == "a" * 64
    assert media_asset_id(first) == media_asset_id(second)
    assert media_asset_id(_video()) != media_asset_id(
        _video(video_start_sec=0, video_end_sec=12)
    )


@pytest.mark.parametrize(
    "source",
    [
        _image(sha256="not-a-hash"),
        _image(object_id="https://private.example/image"),
        _image(video_start_sec=1),
        _video(video_end_sec=400),
        _video(video_start_sec=20, video_end_sec=10),
    ],
)
def test_source_rejects_unbounded_or_ambiguous_media(source):
    with pytest.raises(MediaIngestionError, match="invalid|range|cannot"):
        source.normalized()


def test_duplicate_enqueue_and_processing_call_provider_once():
    provider = _Provider()
    store = InMemoryMediaEmbeddingStore()
    ingestor = MediaEmbeddingIngestor(provider, store)

    first = ingestor.enqueue(_image())
    duplicate = ingestor.enqueue(_image(sha256="a" * 64))
    assert duplicate == first

    assert ingestor.process(first.job_id).state == "ready"
    assert ingestor.process(first.job_id).state == "ready"
    assert len(provider.calls) == 1
    assert len(store.embeddings) == 1
    assert store.get_asset(first.asset_id).embedding == (1.0, 0.0, 0.0, 0.0)


def test_transient_provider_failure_retries_and_preserves_safe_code():
    provider = _Provider(failures=1)
    store = InMemoryMediaEmbeddingStore()
    ingestor = MediaEmbeddingIngestor(provider, store, max_attempts=3)
    job = ingestor.enqueue(_image())

    result = ingestor.process(job.job_id)

    assert result.state == "ready"
    assert result.attempts == 2
    assert len(provider.calls) == 2
    assert store.get_asset(job.asset_id).error_code is None


def test_permanent_failure_is_stable_until_operator_retry():
    provider = _Provider(failures=10)
    store = InMemoryMediaEmbeddingStore()
    ingestor = MediaEmbeddingIngestor(provider, store, max_attempts=2)
    job = ingestor.enqueue(_image())

    failed = ingestor.process(job.job_id)
    again = ingestor.process(job.job_id)

    assert failed.state == "failed"
    assert failed.attempts == 2
    assert failed.error_code == "wemm_encode_failed"
    assert again == failed
    assert len(provider.calls) == 2

    ingestor.retry_failed(job.job_id)
    provider.failures = 0
    recovered = ingestor.process(job.job_id)
    assert recovered.state == "ready"
    assert recovered.attempts == 1
    assert len(provider.calls) == 3


def test_sink_and_provider_contract_failures_are_mapped_without_raw_details():
    provider = _Provider()
    store = InMemoryMediaEmbeddingStore()
    ingestor = MediaEmbeddingIngestor(provider, store, max_attempts=1)
    job = ingestor.enqueue(_image())

    # The fake store deliberately exposes the required sink method; replacing
    # it with a non-callable value exercises the stable failure state.
    store.write_embedding = None
    result = ingestor.process(job.job_id)

    assert result.state == "failed"
    assert result.error_code == "media_sink_unavailable"


def test_invalid_provider_output_fails_without_retrying_or_reembedding():
    provider = _Provider()
    provider.embed_multimodal = lambda _inputs: [[1.0]]
    store = InMemoryMediaEmbeddingStore()
    ingestor = MediaEmbeddingIngestor(provider, store, max_attempts=3)
    job = ingestor.enqueue(_image())

    result = ingestor.process(job.job_id)

    assert result.state == "failed"
    assert result.error_code == "embedding_output_invalid"


def test_object_store_resolver_verifies_hash_and_cleans_materialized_file():
    body = b"image-bytes"
    store = _ObjectStore({"objects/image-1": body})
    resolver = ObjectStoreMediaResolver(store, max_bytes=1024)
    reference = _image().object_reference()
    reference = replace(
        reference, content_hash=hashlib.sha256(body).hexdigest()
    )

    with resolver.resolve(reference) as path:
        materialized = Path(path)
        assert materialized.read_bytes() == body
        assert materialized.exists()
    assert not materialized.exists()
    assert store.calls == [("objects/image-1", 1024)]


def test_object_store_resolver_maps_hash_mismatch_to_stable_provider_error():
    store = _ObjectStore({"objects/image-1": b"wrong"})
    resolver = ObjectStoreMediaResolver(store, max_bytes=1024)

    with pytest.raises(EmbeddingError) as caught:
        with resolver.resolve(_image().object_reference()):
            pass
    assert getattr(caught.value, "code", None) == "wemm_media_resolution_failed"
