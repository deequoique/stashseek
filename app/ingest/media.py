"""Database-free media embedding ingestion contracts and fake driver.

The production media tables are deliberately implemented in a later Trellis
task.  This module keeps the worker boundary useful before that migration by
providing strict input normalization, deterministic identities, and an
in-memory sink that exercises the same idempotency and retry semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import os
import re
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from typing import Literal, Protocol

from app.ingest.embed import (
    EmbeddingError,
    EmbeddingSpaceMetadata,
    MediaObjectReference,
    MultimodalEmbeddingProvider,
    MultimodalInput,
)


MediaType = Literal["image", "video"]
MediaJobState = Literal["pending", "embedding", "ready", "failed"]
MEDIA_ASSET_ID_PREFIX = "ma_"
MEDIA_JOB_ID_PREFIX = "mej_"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_OBJECT_ID_LENGTH = 512
MAX_VIDEO_DURATION_SECONDS = 300.0
MAX_RETRY_ATTEMPTS = 5


class MediaIngestionError(RuntimeError):
    """Stable worker-facing error without provider or object-store details."""

    def __init__(self, message: str, *, code: str = "media_ingestion_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaIngestSource:
    """One tenant-owned image or bounded video slice to embed.

    ``object_id`` is intentionally opaque.  It is passed to an authorized
    resolver, never to the model or an external provider as a URL.
    """

    tenant_id: int
    content_item_id: int
    media_type: MediaType
    object_id: str
    sha256: str
    segment_id: int | None = None
    video_start_sec: float | None = None
    video_end_sec: float | None = None

    def normalized(self) -> "MediaIngestSource":
        if type(self.tenant_id) is not int or self.tenant_id <= 0:
            raise MediaIngestionError("invalid media tenant", code="invalid_media_input")
        if type(self.content_item_id) is not int or self.content_item_id <= 0:
            raise MediaIngestionError("invalid media content item", code="invalid_media_input")
        if self.media_type not in {"image", "video"}:
            raise MediaIngestionError("invalid media type", code="invalid_media_input")
        if (
            not isinstance(self.object_id, str)
            or not self.object_id
            or len(self.object_id) > MAX_OBJECT_ID_LENGTH
            or any(char.isspace() or ord(char) < 32 for char in self.object_id)
            or "//" in self.object_id
            or self.object_id.startswith(("/", "\\"))
        ):
            raise MediaIngestionError("invalid media object", code="invalid_media_input")
        if not isinstance(self.sha256, str) or SHA256_RE.fullmatch(self.sha256) is None:
            raise MediaIngestionError("invalid media hash", code="invalid_media_input")
        if self.segment_id is not None and (
            type(self.segment_id) is not int or self.segment_id <= 0
        ):
            raise MediaIngestionError("invalid media segment", code="invalid_media_input")

        start, end = self.video_start_sec, self.video_end_sec
        if self.media_type == "image" and (start is not None or end is not None):
            raise MediaIngestionError("image cannot have a video range", code="invalid_media_input")
        if self.media_type == "video":
            if start is None or end is None:
                raise MediaIngestionError("video range is required", code="invalid_media_input")
            if (
                type(start) not in (int, float)
                or type(end) not in (int, float)
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or float(start) < 0
                or float(end) <= float(start)
                or float(end) - float(start) > MAX_VIDEO_DURATION_SECONDS
            ):
                raise MediaIngestionError("video range is invalid", code="invalid_media_input")
            start, end = float(start), float(end)
        return replace(
            self,
            object_id=self.object_id.strip(),
            sha256=self.sha256.lower(),
            video_start_sec=start,
            video_end_sec=end,
        )

    def object_reference(self) -> MediaObjectReference:
        source = self.normalized()
        return MediaObjectReference(source.object_id, source.media_type, source.sha256)


def media_asset_id(source: MediaIngestSource) -> str:
    """Return a stable identity for one tenant-scoped media slice."""

    source = source.normalized()
    canonical = "\x1f".join(
        (
            str(source.tenant_id),
            str(source.content_item_id),
            str(source.segment_id or ""),
            source.media_type,
            source.object_id,
            source.sha256,
            "" if source.video_start_sec is None else f"{source.video_start_sec:.3f}",
            "" if source.video_end_sec is None else f"{source.video_end_sec:.3f}",
        )
    )
    return MEDIA_ASSET_ID_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MediaAssetRecord:
    asset_id: str
    source: MediaIngestSource
    state: MediaJobState
    attempts: int = 0
    error_code: str | None = None
    metadata: EmbeddingSpaceMetadata | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class MediaEmbeddingJob:
    job_id: str
    asset_id: str
    embedding_version: int = 1
    state: MediaJobState = "pending"
    attempts: int = 0
    error_code: str | None = None


class MediaEmbeddingStore(Protocol):
    def enqueue(self, source: MediaIngestSource, *, embedding_version: int) -> MediaEmbeddingJob: ...

    def get_job(self, job_id: str) -> MediaEmbeddingJob | None: ...

    def get_asset(self, asset_id: str) -> MediaAssetRecord | None: ...

    def set_job(self, job: MediaEmbeddingJob) -> None: ...

    def set_asset(self, asset: MediaAssetRecord) -> None: ...

    def has_embedding(
        self,
        *,
        asset_id: str,
        metadata: EmbeddingSpaceMetadata,
        embedding_version: int,
    ) -> bool: ...

    def write_embedding(
        self,
        *,
        asset_id: str,
        metadata: EmbeddingSpaceMetadata,
        embedding_version: int,
        embedding: list[float],
    ) -> None: ...


class ObjectStoreMediaResolver:
    """Materialize one authorized object-store reference for local WeMM."""

    def __init__(self, object_store, *, max_bytes: int) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.object_store = object_store
        self.max_bytes = max_bytes

    @contextmanager
    def resolve(self, reference: MediaObjectReference):
        path: str | None = None
        try:
            body = self.object_store.get(reference.object_id, max_bytes=self.max_bytes)
            if not isinstance(body, (bytes, bytearray, memoryview)):
                raise ValueError("object store returned invalid media")
            body = bytes(body)
            if reference.content_hash is not None:
                digest = hashlib.sha256(body).hexdigest()
                if digest != reference.content_hash.lower():
                    raise ValueError("media content hash mismatch")
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="notebook-agent-media-", suffix=".bin", delete=False
            ) as handle:
                handle.write(body)
                path = handle.name
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                "authorized media resolution failed",
                code="wemm_media_resolution_failed",
            ) from exc
        try:
            assert path is not None
            yield path
        finally:
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


class InMemoryMediaEmbeddingStore:
    """Fake sink used before the additive media schema is deployed."""

    def __init__(self) -> None:
        self.jobs: dict[str, MediaEmbeddingJob] = {}
        self.assets: dict[str, MediaAssetRecord] = {}
        self.embeddings: dict[
            tuple[str, str, str | None, str, int, int], tuple[float, ...]
        ] = {}

    def enqueue(self, source: MediaIngestSource, *, embedding_version: int) -> MediaEmbeddingJob:
        source = source.normalized()
        asset_id = media_asset_id(source)
        job_id = MEDIA_JOB_ID_PREFIX + hashlib.sha256(
            f"{asset_id}:{embedding_version}".encode("ascii")
        ).hexdigest()
        existing = self.jobs.get(job_id)
        if existing is not None:
            return existing
        self.assets[asset_id] = MediaAssetRecord(
            asset_id=asset_id,
            source=source,
            state="pending",
        )
        job = MediaEmbeddingJob(
            job_id=job_id,
            asset_id=asset_id,
            embedding_version=embedding_version,
        )
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> MediaEmbeddingJob | None:
        return self.jobs.get(job_id)

    def get_asset(self, asset_id: str) -> MediaAssetRecord | None:
        return self.assets.get(asset_id)

    def set_job(self, job: MediaEmbeddingJob) -> None:
        self.jobs[job.job_id] = job

    def set_asset(self, asset: MediaAssetRecord) -> None:
        self.assets[asset.asset_id] = asset

    def has_embedding(
        self,
        *,
        asset_id: str,
        metadata: EmbeddingSpaceMetadata,
        embedding_version: int,
    ) -> bool:
        return (
            asset_id,
            metadata.model,
            metadata.revision,
            metadata.protocol_version,
            metadata.dimensions,
            embedding_version,
        ) in self.embeddings

    def write_embedding(
        self,
        *,
        asset_id: str,
        metadata: EmbeddingSpaceMetadata,
        embedding_version: int,
        embedding: list[float],
    ) -> None:
        self.embeddings[
            (
                asset_id,
                metadata.model,
                metadata.revision,
                metadata.protocol_version,
                metadata.dimensions,
                embedding_version,
            )
        ] = tuple(embedding)


class MediaEmbeddingIngestor:
    """Bounded provider driver with deterministic enqueue and retry semantics."""

    def __init__(
        self,
        provider: MultimodalEmbeddingProvider,
        store: MediaEmbeddingStore,
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        retry_delay_seconds: float = 0.0,
        embedding_version: int = 1,
    ) -> None:
        if not callable(getattr(provider, "embed_multimodal", None)):
            raise ValueError("media ingestion requires a multimodal provider")
        if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_RETRY_ATTEMPTS:
            raise ValueError("max_attempts is out of bounds")
        if type(embedding_version) is not int or embedding_version <= 0:
            raise ValueError("embedding_version must be positive")
        if (
            type(retry_delay_seconds) not in (int, float)
            or not math.isfinite(float(retry_delay_seconds))
            or retry_delay_seconds < 0
        ):
            raise ValueError("retry_delay_seconds must be non-negative")
        self.provider = provider
        self.store = store
        self.max_attempts = max_attempts
        self.sleep = sleep or (lambda _seconds: None)
        self.retry_delay_seconds = float(retry_delay_seconds)
        self.embedding_version = embedding_version

    def enqueue(self, source: MediaIngestSource) -> MediaEmbeddingJob:
        return self.store.enqueue(source, embedding_version=self.embedding_version)

    def retry_failed(self, job_id: str) -> MediaEmbeddingJob:
        job = self._require_job(job_id)
        if job.state != "failed":
            return job
        reset = replace(job, state="pending", attempts=0, error_code=None)
        self.store.set_job(reset)
        asset = self.store.get_asset(job.asset_id)
        if asset is not None:
            self.store.set_asset(replace(asset, state="pending", attempts=0, error_code=None))
        return reset

    def process(self, job_id: str) -> MediaEmbeddingJob:
        job = self._require_job(job_id)
        asset = self.store.get_asset(job.asset_id)
        if asset is None:
            raise MediaIngestionError("media asset is missing", code="media_asset_missing")
        source = asset.source.normalized()
        try:
            metadata = self._metadata()
        except MediaIngestionError as exc:
            failed = replace(job, state="failed", error_code=exc.code)
            self.store.set_job(failed)
            self.store.set_asset(
                replace(asset, state="failed", error_code=exc.code)
            )
            return failed
        if self.store.has_embedding(
            asset_id=job.asset_id,
            metadata=metadata,
            embedding_version=job.embedding_version,
        ):
            ready = replace(
                job,
                state="ready",
                error_code=None,
            )
            self.store.set_job(ready)
            self.store.set_asset(replace(asset, state="ready", metadata=metadata, error_code=None))
            return ready
        if job.state == "ready":
            return job
        if job.state == "failed" and job.attempts >= self.max_attempts:
            return job

        while job.attempts < self.max_attempts:
            attempts = job.attempts + 1
            job = replace(job, state="embedding", attempts=attempts, error_code=None)
            self.store.set_job(job)
            self.store.set_asset(replace(asset, state="embedding", attempts=attempts, error_code=None))
            try:
                raw = self.provider.embed_multimodal(
                    [MultimodalInput(source.media_type, source.object_reference())]
                )
                vector = self._validate_vector(raw, metadata.dimensions)
                writer = getattr(self.store, "write_embedding", None)
                if not callable(writer):
                    raise MediaIngestionError("embedding sink is incomplete", code="media_sink_unavailable")
                writer(
                    asset_id=job.asset_id,
                    metadata=metadata,
                    embedding_version=job.embedding_version,
                    embedding=vector,
                )
                ready = replace(job, state="ready", error_code=None)
                self.store.set_job(ready)
                self.store.set_asset(
                    replace(asset, state="ready", attempts=attempts, metadata=metadata, embedding=tuple(vector), error_code=None)
                )
                return ready
            except Exception as exc:
                code = self._safe_error_code(exc)
                if attempts >= self.max_attempts or not self._should_retry(exc):
                    failed = replace(job, state="failed", error_code=code)
                    self.store.set_job(failed)
                    self.store.set_asset(replace(asset, state="failed", attempts=attempts, error_code=code))
                    return failed
                job = replace(job, state="pending", error_code=code)
                self.store.set_job(job)
                self.store.set_asset(replace(asset, state="pending", attempts=attempts, error_code=code))
                if self.retry_delay_seconds:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempts - 1)))
        return job

    def _require_job(self, job_id: str) -> MediaEmbeddingJob:
        if not isinstance(job_id, str) or not job_id:
            raise MediaIngestionError("media job is invalid", code="invalid_media_job")
        job = self.store.get_job(job_id)
        if job is None:
            raise MediaIngestionError("media job is missing", code="media_job_missing")
        return job

    def _metadata(self) -> EmbeddingSpaceMetadata:
        metadata = getattr(self.provider, "metadata", None)
        if not isinstance(metadata, EmbeddingSpaceMetadata) or metadata.dimensions <= 0:
            raise MediaIngestionError("embedding space metadata is invalid", code="embedding_space_invalid")
        return metadata

    @staticmethod
    def _validate_vector(raw: object, dimensions: int) -> list[float]:
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], list):
            raise MediaIngestionError("embedding output is invalid", code="embedding_output_invalid")
        vector = raw[0]
        if len(vector) != dimensions:
            raise MediaIngestionError("embedding output dimension is invalid", code="embedding_output_invalid")
        normalized: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MediaIngestionError("embedding output is invalid", code="embedding_output_invalid")
            converted = float(value)
            if not math.isfinite(converted):
                raise MediaIngestionError("embedding output is invalid", code="embedding_output_invalid")
            normalized.append(converted)
        return normalized

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code):
            return code
        return "media_embedding_failed"

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        if isinstance(exc, MediaIngestionError):
            return exc.code in {
                "media_embedding_failed",
                "wemm_media_resolution_failed",
                "wemm_model_load_failed",
                "wemm_encode_failed",
            }
        if isinstance(exc, EmbeddingError):
            return exc.code not in {
                "invalid_input",
                "wemm_dependency_missing",
                "wemm_dimensions_unavailable",
                "wemm_dimension_unsupported",
                "wemm_invalid_output",
                "wemm_zero_vector",
            }
        return True


__all__ = [
    "MAX_VIDEO_DURATION_SECONDS",
    "MediaAssetRecord",
    "MediaEmbeddingIngestor",
    "MediaEmbeddingJob",
    "MediaEmbeddingStore",
    "MediaIngestSource",
    "MediaIngestionError",
    "InMemoryMediaEmbeddingStore",
    "ObjectStoreMediaResolver",
    "media_asset_id",
]
