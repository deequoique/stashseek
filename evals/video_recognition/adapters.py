"""Provider-neutral adapters with an explicit no-provider execution gate.

The first implementation intentionally ships fixture adapters and provider
preflight only.  A provider adapter never guesses credentials or sends a
request merely because an environment variable happens to exist; real calls
must be added behind a separately reviewed ``allow_provider_calls`` boundary.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias

from .protocol import ASRResponse, OCRResponse, VisionResponse, stable_error
from .schema import Profile, TimeRange


class AdapterError(RuntimeError):
    """Base class for bounded adapter failures."""


class ProviderCredentialsMissing(AdapterError):
    """The selected profile has no complete credential set in the environment."""

    error_code = "provider_credentials_missing"


class ProviderExecutionDisabled(AdapterError):
    """Paid/network execution is disabled for this benchmark harness."""

    error_code = "provider_execution_disabled"


class AdapterInputError(AdapterError):
    """An input violated the minimum-media protocol boundary."""

    error_code = "adapter_input_invalid"


@dataclass(frozen=True)
class AudioWindowInput:
    sample_id: str
    window_id: str
    time_range: TimeRange
    payload: bytes

    def __post_init__(self) -> None:
        if self.time_range.duration_sec > 60:
            raise AdapterInputError("audio window exceeds the 60 second boundary")
        if not isinstance(self.payload, bytes) or not self.payload:
            raise AdapterInputError("audio window payload is empty")
        if len(self.payload) > 25 * 1024 * 1024:
            raise AdapterInputError("audio window exceeds bounded payload size")


@dataclass(frozen=True)
class FrameInput:
    sample_id: str
    frame_id: str
    timestamp_sec: float
    payload: bytes

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp_sec) or self.timestamp_sec < 0:
            raise AdapterInputError("frame timestamp must be non-negative")
        if not isinstance(self.payload, bytes) or not self.payload:
            raise AdapterInputError("frame payload is empty")
        if len(self.payload) > 10 * 1024 * 1024:
            raise AdapterInputError("frame payload exceeds bounded size")


@dataclass(frozen=True)
class FrameBundleInput:
    sample_id: str
    bundle_id: str
    frames: tuple[FrameInput, ...]

    def __post_init__(self) -> None:
        if not 3 <= len(self.frames) <= 6:
            raise AdapterInputError("frame bundle must contain three to six frames")
        if any(frame.sample_id != self.sample_id for frame in self.frames):
            raise AdapterInputError("frame/sample identity mismatch")
        if len({frame.frame_id for frame in self.frames}) != len(self.frames):
            raise AdapterInputError("frame IDs must be unique within a bundle")
        if len({frame.timestamp_sec for frame in self.frames}) != len(self.frames):
            raise AdapterInputError("frame timestamps must be unique within a bundle")


AdapterResponse: TypeAlias = ASRResponse | VisionResponse | OCRResponse


class BenchmarkAdapter(Protocol):
    profile: Profile

    def run(self, *, run_id: str, sample_id: str, input_unit: AudioWindowInput | FrameBundleInput | FrameInput) -> AdapterResponse: ...


@dataclass(frozen=True)
class CredentialStatus:
    profile_id: str
    provider: str
    required_env: tuple[str, ...]
    available: bool
    missing_env: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "required_env_count": len(self.required_env),
            "available": self.available,
            "missing_env": list(self.missing_env),
        }


def credential_status(profile: Profile, environ: dict[str, str] | None = None) -> CredentialStatus:
    """Inspect presence only; never return credential values."""

    values = os.environ if environ is None else environ
    required = tuple(profile.required_env)
    missing = tuple(name for name in required if not values.get(name, "").strip())
    return CredentialStatus(profile.profile_id, profile.provider, required, not missing, missing)


@dataclass
class ProviderAdapter:
    """A deliberate hard stop until a provider implementation is approved."""

    profile: Profile
    environ: dict[str, str] | None = None
    allow_provider_calls: bool = False

    def run(self, *, run_id: str, sample_id: str, input_unit: AudioWindowInput | FrameBundleInput | FrameInput) -> AdapterResponse:
        if input_unit.sample_id != sample_id:
            raise AdapterInputError("adapter sample identity mismatch")
        status = credential_status(self.profile, self.environ)
        if not status.available:
            raise ProviderCredentialsMissing("provider credentials are unavailable")
        if not self.allow_provider_calls:
            raise ProviderExecutionDisabled("provider execution is disabled")
        # A concrete adapter must be implemented and reviewed per provider;
        # keeping this branch closed prevents a hidden paid call.
        raise ProviderExecutionDisabled("no provider implementation is enabled")

    def safe_failure(self, exc: Exception) -> dict[str, object]:
        status = credential_status(self.profile, self.environ)
        if isinstance(exc, ProviderCredentialsMissing):
            code = ProviderCredentialsMissing.error_code
        elif isinstance(exc, ProviderExecutionDisabled):
            code = ProviderExecutionDisabled.error_code
        else:
            code = "provider_adapter_failed"
        return {
            "error": stable_error(
                code,
                error_class=type(exc).__name__,
                retryable=code == "provider_adapter_failed",
                phase="provider",
            ).model_dump(mode="json"),
            "credential_available": status.available,
        }


def _load_fixture(path: Path, model: type[AdapterResponse]) -> AdapterResponse:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError("protocol fixture unavailable") from exc
    return model.model_validate(payload)


@dataclass
class FixtureAdapter:
    """Deterministic protocol adapter used by dry-run and unit tests."""

    profile: Profile
    fixture_dir: Path

    def run(self, *, run_id: str, sample_id: str, input_unit: AudioWindowInput | FrameBundleInput | FrameInput) -> AdapterResponse:
        started = time.perf_counter()
        if self.profile.modality == "asr":
            if not isinstance(input_unit, AudioWindowInput):
                raise AdapterInputError("ASR requires an audio window")
            model = ASRResponse
            path = self.fixture_dir / "asr_success.json"
        elif self.profile.modality == "vision":
            if not isinstance(input_unit, FrameBundleInput):
                raise AdapterInputError("Vision requires a frame bundle")
            model = VisionResponse
            path = self.fixture_dir / "vision_success.json"
        else:
            if not isinstance(input_unit, FrameInput):
                raise AdapterInputError("OCR requires a single frame")
            model = OCRResponse
            path = self.fixture_dir / "ocr_success.json"
        response = _load_fixture(path, model)
        latency_ms = max(0.01, (time.perf_counter() - started) * 1000)
        if input_unit.sample_id != sample_id:
            raise AdapterInputError("adapter sample identity mismatch")
        updates: dict[str, object] = {
            "run_id": run_id,
            "sample_id": sample_id,
            "provider": self.profile.provider,
            "model_version": self.profile.model_alias,
            "latency_ms": latency_ms,
        }
        if isinstance(response, ASRResponse):
            updates["input_window_id"] = input_unit.window_id
            offset = input_unit.time_range.start_sec
            updates["segments"] = [
                segment.model_copy(
                    update={
                        "start_sec": segment.start_sec + offset,
                        "end_sec": segment.end_sec + offset,
                        "words": [
                            word.model_copy(update={"start_sec": word.start_sec + offset, "end_sec": word.end_sec + offset})
                            for word in segment.words
                        ],
                    }
                )
                for segment in response.segments
            ]
        elif isinstance(response, VisionResponse):
            updates["input_bundle_id"] = input_unit.bundle_id
            frame_times = [frame.timestamp_sec for frame in input_unit.frames]
            updates["observations"] = [
                observation.model_copy(
                    update={"evidence_frame_times": frame_times[: len(observation.evidence_frame_times)]}
                )
                for observation in response.observations
            ]
        else:
            updates["input_frame_id"] = input_unit.frame_id
            updates["detections"] = [
                detection.model_copy(update={"frame_timestamp_sec": input_unit.timestamp_sec})
                for detection in response.detections
            ]
        normalized = type(response).model_validate({**response.model_dump(mode="python"), **updates})
        _validate_response_boundary(self.profile, input_unit, normalized)
        return normalized


def _validate_response_boundary(
    profile: Profile,
    input_unit: AudioWindowInput | FrameBundleInput | FrameInput,
    response: AdapterResponse,
) -> None:
    if response.sample_id != input_unit.sample_id or response.provider != profile.provider:
        raise AdapterInputError("normalized response identity mismatch")
    if isinstance(response, ASRResponse) and isinstance(input_unit, AudioWindowInput):
        if response.input_window_id != input_unit.window_id:
            raise AdapterInputError("ASR response input identity mismatch")
        if any(
            segment.start_sec < input_unit.time_range.start_sec
            or segment.end_sec > input_unit.time_range.end_sec
            for segment in response.segments
        ):
            raise AdapterInputError("ASR timestamps fall outside the input window")
        if response.usage.audio_seconds > input_unit.time_range.duration_sec:
            raise AdapterInputError("ASR usage exceeds the input window")
    elif isinstance(response, VisionResponse) and isinstance(input_unit, FrameBundleInput):
        if response.input_bundle_id != input_unit.bundle_id:
            raise AdapterInputError("Vision response input identity mismatch")
        allowed = {frame.timestamp_sec for frame in input_unit.frames}
        if any(time not in allowed for item in response.observations for time in item.evidence_frame_times):
            raise AdapterInputError("Vision evidence references a frame outside the input bundle")
        if response.usage.image_count > len(input_unit.frames):
            raise AdapterInputError("Vision usage exceeds the input bundle")
    elif isinstance(response, OCRResponse) and isinstance(input_unit, FrameInput):
        if response.input_frame_id != input_unit.frame_id:
            raise AdapterInputError("OCR response input identity mismatch")
        if any(item.frame_timestamp_sec != input_unit.timestamp_sec for item in response.detections):
            raise AdapterInputError("OCR detection references a different frame")
        if response.usage.image_count > 1:
            raise AdapterInputError("OCR usage exceeds one input frame")
    else:
        raise AdapterInputError("normalized response modality does not match its input")


def adapter_for_profile(
    profile: Profile,
    *,
    fixture_dir: str | Path | None = None,
    mode: str = "fixture",
    environ: dict[str, str] | None = None,
    allow_provider_calls: bool = False,
) -> BenchmarkAdapter:
    """Build a fixture adapter or an explicitly gated provider adapter."""

    if mode == "fixture":
        if fixture_dir is None:
            fixture_dir = Path(__file__).with_name("fixtures")
        return FixtureAdapter(profile, Path(fixture_dir))
    if mode == "provider":
        return ProviderAdapter(profile, environ, allow_provider_calls)
    raise ValueError("adapter mode must be fixture or provider")
