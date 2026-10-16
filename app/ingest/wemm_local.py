"""Lazy local WeMM-Embedding adapter backed by Sentence Transformers.

This module intentionally contains no top-level import of the heavyweight
Sentence Transformers runtime. The optional runtime is loaded only on the
first encode call, and the model loader is injectable so contract checks can
run without downloading or executing a model.
"""

from __future__ import annotations

import math
import os
import stat
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from typing import Any, Protocol

from app.ingest.embed import (
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingSpaceMetadata,
    MediaObjectReference,
    MultimodalInput,
)


DEFAULT_WEMM_MODEL = "tencent/WeMM-Embedding-2B"
DEFAULT_WEMM_REVISION = "bbd6cd4bf52cfc6716f752a2df80b2706720bd95"
DEFAULT_WEMM_DIMENSIONS = 1024
DEFAULT_WEMM_DEVICE = "cpu"
DEFAULT_WEMM_BATCH_SIZE = 1
DEFAULT_WEMM_MAX_INPUTS = 32
DEFAULT_WEMM_MAX_TEXT_CHARS = 8_192
DEFAULT_WEMM_MAX_MEDIA_BYTES = 128 * 1024 * 1024
WEMM_LOCAL_PROTOCOL_VERSION = "sentence-transformers-local.v1"


class MediaResolver(Protocol):
    """Resolve an authorized object reference for one encode call.

    The returned context manager owns the lifetime of the local input. It may
    download or materialize a temporary file on enter and must clean it up on
    exit. The adapter never receives a URL or sends the opaque object ID to
    the model.
    """

    def resolve(
        self, reference: MediaObjectReference
    ) -> AbstractContextManager[str | os.PathLike[str]]: ...


MediaResolverLike = MediaResolver | Callable[
    [MediaObjectReference], AbstractContextManager[str | os.PathLike[str]]
]
ModelLoader = Callable[[str, str, str], Any]


def _load_sentence_transformer(model: str, revision: str, device: str) -> Any:
    """Load the upstream runtime only when the provider is first used."""

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingError(
            "WeMM local runtime is unavailable",
            code="wemm_dependency_missing",
        ) from exc

    try:
        return SentenceTransformer(
            model,
            revision=revision,
            trust_remote_code=True,
            device=device,
        )
    except Exception as exc:
        raise EmbeddingError(
            "WeMM model load failed",
            code="wemm_model_load_failed",
        ) from exc


class WemmLocalEmbedder:
    """Optional local WeMM provider using the official Sentence Transformers path.

    Construction is configuration-only: it imports no optional dependency,
    loads no model, and performs no network or filesystem materialization.
    Model loading and remote-code execution happen once, lazily, on the first
    text or media encode call.
    """

    MAX_BATCH_SIZE = 8
    MAX_INPUTS = DEFAULT_WEMM_MAX_INPUTS
    MAX_TEXT_CHARS = DEFAULT_WEMM_MAX_TEXT_CHARS
    MAX_MEDIA_BYTES = DEFAULT_WEMM_MAX_MEDIA_BYTES
    MIN_VECTOR_NORM = 1e-12

    def __init__(
        self,
        *,
        model: str = DEFAULT_WEMM_MODEL,
        revision: str = DEFAULT_WEMM_REVISION,
        dimensions: int = DEFAULT_WEMM_DIMENSIONS,
        device: str = DEFAULT_WEMM_DEVICE,
        batch_size: int = DEFAULT_WEMM_BATCH_SIZE,
        max_inputs: int = DEFAULT_WEMM_MAX_INPUTS,
        max_text_chars: int = DEFAULT_WEMM_MAX_TEXT_CHARS,
        max_media_bytes: int = DEFAULT_WEMM_MAX_MEDIA_BYTES,
        media_resolver: MediaResolverLike | None = None,
        model_loader: ModelLoader | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("WEMM_MODEL must not be empty")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("WEMM_REVISION is required for local WeMM")
        if revision != revision.strip() or any(
            character.isspace() for character in revision
        ):
            raise ValueError("WEMM_REVISION must be a non-whitespace revision")
        if len(revision) != 40 or any(
            character not in "0123456789abcdef" for character in revision
        ):
            raise ValueError("WEMM_REVISION must be a 40-character commit SHA")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
            raise ValueError("WEMM_DIMENSIONS must be positive")
        if not isinstance(device, str) or not device.strip() or any(
            character.isspace() for character in device
        ):
            raise ValueError("WEMM_DEVICE must be a non-whitespace device")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= self.MAX_BATCH_SIZE
        ):
            raise ValueError(
                f"WEMM_BATCH_SIZE must be between 1 and {self.MAX_BATCH_SIZE}"
            )
        if (
            not isinstance(max_inputs, int)
            or isinstance(max_inputs, bool)
            or not 1 <= max_inputs <= self.MAX_INPUTS
        ):
            raise ValueError(
                f"WEMM_MAX_INPUTS must be between 1 and {self.MAX_INPUTS}"
            )
        if (
            not isinstance(max_text_chars, int)
            or isinstance(max_text_chars, bool)
            or not 1 <= max_text_chars <= self.MAX_TEXT_CHARS
        ):
            raise ValueError(
                f"WEMM_MAX_TEXT_CHARS must be between 1 and {self.MAX_TEXT_CHARS}"
            )
        if (
            not isinstance(max_media_bytes, int)
            or isinstance(max_media_bytes, bool)
            or not 1 <= max_media_bytes <= self.MAX_MEDIA_BYTES
        ):
            raise ValueError(
                "WEMM_MAX_MEDIA_BYTES must be between 1 and "
                f"{self.MAX_MEDIA_BYTES}"
            )

        self.model = model
        self.revision = revision
        self.device = device
        self.batch_size = batch_size
        self.max_inputs = max_inputs
        self.max_text_chars = max_text_chars
        self.max_media_bytes = max_media_bytes
        self.media_resolver = media_resolver
        self.metadata = EmbeddingSpaceMetadata(
            model=model,
            protocol_version=WEMM_LOCAL_PROTOCOL_VERSION,
            dimensions=dimensions,
            revision=revision,
        )
        self._model_loader = model_loader or _load_sentence_transformer
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    @property
    def dimensions(self) -> int:
        return self.metadata.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text in the same local WeMM space without a media resolver."""

        if not isinstance(texts, list):
            raise EmbeddingInputError("text inputs must be a list")
        return self.embed_multimodal(
            [MultimodalInput("text", text) for text in texts]
        )

    def embed_multimodal(
        self, inputs: list[MultimodalInput]
    ) -> list[list[float]]:
        if not isinstance(inputs, list):
            raise EmbeddingInputError("multimodal inputs must be a list")
        if not inputs:
            return []
        if len(inputs) > self.max_inputs:
            raise EmbeddingInputError("multimodal input count exceeds limit")

        for item in inputs:
            self._validate_input(item)
        if (
            any(item.modality != "text" for item in inputs)
            and self.media_resolver is None
        ):
            raise EmbeddingInputError(
                "media embedding requires an authorized media resolver"
            )

        model = self._get_model()
        embeddings: list[list[float]] = []
        for start in range(0, len(inputs), self.batch_size):
            batch = inputs[start : start + self.batch_size]
            with ExitStack() as stack:
                model_inputs = [
                    self._materialize_input(item, stack) for item in batch
                ]
                raw_embeddings = self._encode(model, model_inputs)
                embeddings.extend(
                    _normalize_output(
                        raw_embeddings,
                        expected_count=len(batch),
                        dimensions=self.dimensions,
                    )
                )
        return embeddings

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                model = self._model_loader(self.model, self.revision, self.device)
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingError(
                    "WeMM model load failed",
                    code="wemm_model_load_failed",
                ) from exc
            _validate_supported_dimensions(model, self.dimensions)
            self._model = model
            return model

    def _encode(self, model: Any, model_inputs: list[object]) -> object:
        try:
            return model.encode(
                model_inputs,
                batch_size=self.batch_size,
                truncate_dim=self.dimensions,
                normalize_embeddings=True,
                convert_to_tensor=True,
            )
        except Exception as exc:
            raise EmbeddingError(
                "WeMM model encode failed",
                code="wemm_encode_failed",
            ) from exc

    def _validate_input(self, item: object) -> None:
        if not isinstance(item, MultimodalInput):
            raise EmbeddingInputError("multimodal input must be a MultimodalInput")
        if item.modality == "text":
            if not isinstance(item.content, str) or not item.content.strip():
                raise EmbeddingInputError("text input must be non-empty")
            if len(item.content) > self.max_text_chars:
                raise EmbeddingInputError("text input exceeds character limit")
            return
        if item.modality not in {"image", "video"} or not isinstance(
            item.content, MediaObjectReference
        ):
            raise EmbeddingInputError("media input must contain a media object reference")
        reference = item.content
        if reference.modality != item.modality:
            raise EmbeddingInputError("media modality does not match object reference")
        object_id = reference.object_id
        if (
            not isinstance(object_id, str)
            or not object_id
            or len(object_id) > 512
            or any(character.isspace() or ord(character) < 32 for character in object_id)
            or "://" in object_id
            or object_id.startswith(("/", "\\"))
        ):
            raise EmbeddingInputError("media object reference is invalid")
        content_hash = reference.content_hash
        if content_hash is not None and (
            not isinstance(content_hash, str)
            or not content_hash
            or len(content_hash) > 128
            or any(character.isspace() or ord(character) < 32 for character in content_hash)
        ):
            raise EmbeddingInputError("media content hash is invalid")

    def _materialize_input(self, item: MultimodalInput, stack: ExitStack) -> object:
        if item.modality == "text":
            assert isinstance(item.content, str)
            return item.content
        if self.media_resolver is None:
            raise EmbeddingInputError(
                "media embedding requires an authorized media resolver"
            )
        reference = item.content
        assert isinstance(reference, MediaObjectReference)
        try:
            resolver = self.media_resolver
            if hasattr(resolver, "resolve"):
                managed_input = resolver.resolve(reference)
            else:
                managed_input = resolver(reference)
            local_input = stack.enter_context(managed_input)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                "authorized media resolution failed",
                code="wemm_media_resolution_failed",
            ) from exc
        local_path = _validate_local_media_input(
            local_input,
            max_media_bytes=self.max_media_bytes,
        )
        return {item.modality: local_path}


def _validate_supported_dimensions(model: Any, dimensions: int) -> None:
    try:
        supported = getattr(
            model[0].auto_model.config,
            "matryoshka_dimensions",
            None,
        )
    except Exception as exc:
        raise EmbeddingError(
            "WeMM model dimension metadata is unavailable",
            code="wemm_dimensions_unavailable",
        ) from exc
    if supported is None:
        raise EmbeddingError(
            "WeMM model dimension metadata is unavailable",
            code="wemm_dimensions_unavailable",
        )
    try:
        supported_dimensions = tuple(int(value) for value in supported)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EmbeddingError(
            "WeMM model dimension metadata is invalid",
            code="wemm_dimensions_unavailable",
        ) from exc
    if dimensions not in supported_dimensions:
        raise EmbeddingError(
            "configured WeMM dimension is unsupported by the model",
            code="wemm_dimension_unsupported",
        )


def _validate_local_media_input(
    local_input: object, *, max_media_bytes: int
) -> str:
    if isinstance(local_input, bytes):
        raise EmbeddingError(
            "authorized media resolver must return a local path",
            code="wemm_media_resolution_failed",
        )
    try:
        path = os.fspath(local_input)
    except TypeError as exc:
        raise EmbeddingError(
            "authorized media resolver must return a local path",
            code="wemm_media_resolution_failed",
        ) from exc
    if isinstance(path, bytes) or not path or not os.path.isabs(path):
        raise EmbeddingError(
            "authorized media resolver must return an absolute local path",
            code="wemm_media_resolution_failed",
        )
    if "\x00" in path or "://" in path:
        raise EmbeddingError(
            "authorized media resolver returned an invalid local path",
            code="wemm_media_resolution_failed",
        )
    try:
        media_stat = os.stat(path)
    except (OSError, ValueError) as exc:
        raise EmbeddingError(
            "authorized media input is unavailable",
            code="wemm_media_resolution_failed",
        ) from exc
    if not stat.S_ISREG(media_stat.st_mode):
        raise EmbeddingError(
            "authorized media input must be a regular file",
            code="wemm_media_resolution_failed",
        )
    if media_stat.st_size <= 0 or media_stat.st_size > max_media_bytes:
        raise EmbeddingError(
            "authorized media input exceeds the size limit",
            code="wemm_media_resolution_failed",
        )
    return path


def _normalize_output(
    raw_embeddings: object, *, expected_count: int, dimensions: int
) -> list[list[float]]:
    try:
        tensor = raw_embeddings
        detach = getattr(tensor, "detach", None)
        if callable(detach):
            tensor = detach()
        cpu = getattr(tensor, "cpu", None)
        if callable(cpu):
            tensor = cpu()
        tolist = getattr(tensor, "tolist", None)
        if callable(tolist):
            tensor = tolist()
    except Exception as exc:
        raise EmbeddingError(
            "WeMM model output is invalid",
            code="wemm_invalid_output",
        ) from exc

    if not isinstance(tensor, list):
        raise EmbeddingError("WeMM model output is invalid", code="wemm_invalid_output")
    if expected_count == 1 and (
        not tensor or not isinstance(tensor[0], (list, tuple))
    ):
        rows: list[object] = [tensor]
    else:
        rows = tensor
    if len(rows) != expected_count:
        raise EmbeddingError(
            "WeMM model output count mismatch",
            code="wemm_invalid_output",
        )
    return [_normalize_unit_vector(row, dimensions) for row in rows]


def _normalize_unit_vector(vector: object, dimensions: int) -> list[float]:
    if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
        raise EmbeddingError(
            "WeMM model output dimension mismatch",
            code="wemm_invalid_output",
        )
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingError(
                "WeMM model output contains invalid values",
                code="wemm_invalid_output",
            )
        try:
            converted = float(value)
        except (OverflowError, ValueError) as exc:
            raise EmbeddingError(
                "WeMM model output contains invalid values",
                code="wemm_invalid_output",
            ) from exc
        if not math.isfinite(converted):
            raise EmbeddingError(
                "WeMM model output contains invalid values",
                code="wemm_invalid_output",
            )
        values.append(converted)

    scale = max(abs(value) for value in values)
    if not math.isfinite(scale) or scale <= WemmLocalEmbedder.MIN_VECTOR_NORM:
        raise EmbeddingError(
            "WeMM model output is a zero vector",
            code="wemm_zero_vector",
        )
    scaled_values = [value / scale for value in values]
    scaled_norm = math.sqrt(math.fsum(value * value for value in scaled_values))
    if (
        not math.isfinite(scaled_norm)
        or scaled_norm <= WemmLocalEmbedder.MIN_VECTOR_NORM
    ):
        raise EmbeddingError(
            "WeMM model output is a zero vector",
            code="wemm_zero_vector",
        )
    normalized = [value / scaled_norm for value in scaled_values]
    if not all(math.isfinite(value) for value in normalized):
        raise EmbeddingError(
            "WeMM model output contains invalid values",
            code="wemm_invalid_output",
        )
    return normalized


__all__ = [
    "DEFAULT_WEMM_BATCH_SIZE",
    "DEFAULT_WEMM_DEVICE",
    "DEFAULT_WEMM_DIMENSIONS",
    "DEFAULT_WEMM_MAX_INPUTS",
    "DEFAULT_WEMM_MAX_MEDIA_BYTES",
    "DEFAULT_WEMM_MAX_TEXT_CHARS",
    "DEFAULT_WEMM_MODEL",
    "DEFAULT_WEMM_REVISION",
    "MediaResolver",
    "MediaResolverLike",
    "ModelLoader",
    "WemmLocalEmbedder",
    "WEMM_LOCAL_PROTOCOL_VERSION",
]
