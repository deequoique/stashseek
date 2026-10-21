"""Formal public metadata/subtitle connector probe.

The live probe uses only public HTTPS metadata/subtitle endpoints through the
existing yt-dlp runtime.  Subtitle bytes are bounded, parsed in memory, and
discarded; signed subtitle URLs and raw responses never enter the snapshot.
The default CLI path reads the checked-in normalized projection so dry runs
remain network-free.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.connectors.bounded_fetch import fetch_bounded
from app.ingest.validate import IngestLimitExceeded
from app.tls import configure_trusted_ca

from .schema import (
    BenchmarkCatalog,
    ProbeRecord,
    ProbeSnapshot,
    Sample,
    SubtitleTrack,
    load_probe_snapshot,
)

MAX_SUBTITLE_BYTES = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 30.0


class ProbeError(RuntimeError):
    """Bounded probe failure with no provider payload attached."""


def _stable_failure(stderr: object, *, timeout: bool = False) -> str:
    text = str(stderr or "").lower()
    if timeout:
        return "connector_probe_timeout"
    if "429" in text or "too many requests" in text or "rate" in text and "limit" in text:
        return "connector_rate_limited"
    if "login" in text or "sign in" in text or "authentication" in text:
        return "connector_auth_required"
    return "connector_probe_failed"


def _normalise_language(value: object) -> str:
    return value.strip().lower().replace("_", "-") if isinstance(value, str) else ""


def _base_language(value: object) -> str:
    return _normalise_language(value).split("-", 1)[0]


def _coverage(cues: list[tuple[float, float]], duration: int | None) -> tuple[int, float | None, float | None, float | None, str]:
    if not cues:
        return 0, None, None, 0.0 if duration else None, "none"
    ordered = sorted((start, end) for start, end in cues if end > start and start >= 0)
    if not ordered:
        return 0, None, None, 0.0 if duration else None, "none"
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    covered = sum(end - start for start, end in merged)
    ratio = min(1.0, covered / duration) if duration and duration > 0 else None
    status = "complete" if ratio is not None and ratio >= 0.80 else "partial" if ratio is not None and ratio >= 0.10 else "none"
    return len(ordered), ordered[0][0], max(end for _, end in ordered), ratio, status


def _parse_json3(body: bytes) -> list[tuple[float, float]]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return []
    cues: list[tuple[float, float]] = []
    for event in payload["events"]:
        if not isinstance(event, dict) or "tStartMs" not in event:
            continue
        segments = event.get("segs")
        text = "".join(segment.get("utf8", "") for segment in segments or [] if isinstance(segment, dict)).strip()
        if not text:
            continue
        try:
            start = float(event["tStartMs"]) / 1000
            end = start + max(0.01, float(event.get("dDurationMs", 0)) / 1000)
        except (TypeError, ValueError):
            continue
        if start >= 0 and end > start:
            cues.append((start, end))
    return cues


def _parse_srt(body: bytes) -> list[tuple[float, float]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return []
    cues: list[tuple[float, float]] = []
    for block in text.replace("\r", "").split("\n\n"):
        lines = [line.strip() for line in block.split("\n")]
        timing = next((line for line in lines[:2] if "-->" in line), None)
        if timing is None:
            continue
        left, right = [part.strip().split()[0] for part in timing.split("-->", 1)]

        def parse(value: str) -> float:
            hours, minutes, seconds = value.replace(",", ".").split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

        try:
            start, end = parse(left), parse(right)
        except (TypeError, ValueError):
            continue
        if end > start:
            cues.append((start, end))
    return cues


def _formats(track: object) -> list[dict[str, Any]]:
    if not isinstance(track, list):
        return []
    return [item for item in track if isinstance(item, dict)]


@dataclass
class ConnectorProbe:
    """Read-only, public connector probe with injectable subprocess/openers."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_subtitle_bytes: int = MAX_SUBTITLE_BYTES
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    urlopen: Callable[..., Any] = urllib.request.urlopen
    proxy_url: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.max_subtitle_bytes <= 0:
            raise ValueError("probe limits must be positive")
        if self.proxy_url is not None:
            parts = urlsplit(self.proxy_url)
            if (
                parts.scheme != "http"
                or parts.hostname not in {"127.0.0.1", "localhost"}
                or parts.port is None
                or parts.username is not None
                or parts.password is not None
                or parts.path
                or parts.query
                or parts.fragment
            ):
                raise ValueError("probe proxy must be a credential-free loopback HTTP URL")

    def _metadata(self, sample: Sample) -> dict[str, Any]:
        environment = None
        if self.proxy_url:
            environment = os.environ.copy()
            environment.pop("ALL_PROXY", None)
            environment.pop("all_proxy", None)
            environment.update({
                "HTTP_PROXY": self.proxy_url,
                "HTTPS_PROXY": self.proxy_url,
                "http_proxy": self.proxy_url,
                "https_proxy": self.proxy_url,
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
            })
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-playlist",
            "--skip-download",
            "--socket-timeout",
            str(self.timeout_seconds),
            "--dump-single-json",
            sample.public_url,
        ]
        kwargs: dict[str, Any] = {"text": True, "capture_output": True, "check": False, "timeout": self.timeout_seconds}
        if environment is not None:
            kwargs["env"] = environment
        try:
            result = self.runner(args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("connector_probe_timeout") from exc
        if result.returncode:
            raise ProbeError(_stable_failure(getattr(result, "stderr", "")))
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProbeError("connector_metadata_invalid") from exc
        if not isinstance(payload, dict):
            raise ProbeError("connector_metadata_invalid")
        resolved_id = payload.get("id")
        if not isinstance(resolved_id, str) or resolved_id.casefold() != sample.platform_id.casefold():
            raise ProbeError("connector_sample_identity_mismatch")
        return payload

    def _fetch_subtitle(self, url: str, headers: object = None) -> bytes:
        if not isinstance(url, str) or not url:
            raise ProbeError("subtitle_body_unavailable")
        try:
            return fetch_bounded(
                url,
                headers=headers if isinstance(headers, dict) else None,
                max_bytes=self.max_subtitle_bytes,
                socket_timeout_seconds=min(10.0, self.timeout_seconds),
                opener=self.urlopen,
            )
        except IngestLimitExceeded as exc:
            raise ProbeError("subtitle_body_too_large") from exc
        except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            raise ProbeError("subtitle_body_probe_failed") from exc

    def _track_projection(self, sample: Sample, payload: dict[str, Any]) -> list[SubtitleTrack]:
        groups: list[tuple[str, object]]
        if sample.platform == "youtube":
            groups = [("manual", payload.get("subtitles") or {}), ("automatic", payload.get("automatic_captions") or {})]
        else:
            groups = [("manual", payload.get("subtitles") or {})]
        tracks: list[SubtitleTrack] = []
        duration = payload.get("duration")
        duration_value = round(duration) if isinstance(duration, (int, float)) and duration >= 0 else sample.duration_sec
        for kind, group in groups:
            if not isinstance(group, dict):
                continue
            for language, formats in group.items():
                if not isinstance(language, str) or language.lower() == "danmaku" or not isinstance(formats, list):
                    continue
                selected = next((item for item in _formats(formats) if item.get("ext") in {"json3", "srt", "vtt"}), None)
                if selected is None:
                    continue
                format_name = str(selected.get("ext") or "unknown")
                cues: list[tuple[float, float]] = []
                body_status = "pending"
                body: bytes | None = None
                if isinstance(selected.get("data"), str):
                    body = selected["data"].encode("utf-8")
                    if len(body) > self.max_subtitle_bytes:
                        body = None
                        body_status = "failed"
                elif sample.platform == "youtube" and isinstance(selected.get("url"), str):
                    try:
                        body = self._fetch_subtitle(selected["url"], selected.get("http_headers") or payload.get("http_headers"))
                    except ProbeError:
                        body_status = "failed"
                if body is not None:
                    body_status = "completed"
                    cues = _parse_json3(body) if format_name == "json3" else _parse_srt(body)
                count, start, end, ratio, status = _coverage(cues, duration_value)
                if body_status != "completed":
                    status = "pending"
                tracks.append(
                    SubtitleTrack(
                        language=language,
                        kind=kind,  # type: ignore[arg-type]
                        format=format_name,
                        body_probe_status=body_status,  # type: ignore[arg-type]
                        cue_count=count if body_status == "completed" else None,
                        content_start_sec=start,
                        content_end_sec=end,
                        coverage_ratio=ratio,
                        coverage_status=status,  # type: ignore[arg-type]
                    )
                )
        return tracks

    def probe_sample(self, sample: Sample) -> ProbeRecord:
        try:
            payload = self._metadata(sample)
        except ProbeError as exc:
            code = str(exc)
            return ProbeRecord(
                sample_id=sample.sample_id,
                platform=sample.platform,
                platform_id=sample.platform_id,
                public_url=sample.public_url,
                checked_at=sample.publicly_accessible_at,
                publicly_accessible=False,
                single_video=False,
                metadata_status="failed",
                metadata_source="yt-dlp",
                probe_status="failed",
                stable_error_code=code if code.replace("_", "").isalnum() else "connector_probe_failed",
                rights_status=sample.rights_status,
                rights_note=sample.rights_evidence_note,
            )
        entries = payload.get("entries")
        single_video = not isinstance(entries, list) or len([entry for entry in entries if entry]) <= 1
        duration = payload.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            return self._failed_record(sample, "connector_metadata_invalid")
        tracks = self._track_projection(sample, payload)
        # A translated track may be inaccessible while the connector-selected
        # original/manual track is fully readable.  Corpus coverage and probe
        # completion therefore follow usable bodies, not every generated
        # translation advertised by yt-dlp.
        subtitle_status = (
            "none"
            if not tracks
            else "complete"
            if any(track.coverage_status == "complete" for track in tracks)
            else "partial"
            if any(track.coverage_status == "partial" for track in tracks)
            else "pending"
        )
        has_usable_body = any(
            track.body_probe_status == "completed"
            and track.coverage_status in {"complete", "partial"}
            for track in tracks
        )
        status = (
            "complete" if has_usable_body else "incomplete" if tracks else "no_platform_track"
        )
        return ProbeRecord(
            sample_id=sample.sample_id,
            platform=sample.platform,
            platform_id=sample.platform_id,
            public_url=sample.public_url,
            checked_at=sample.publicly_accessible_at,
            publicly_accessible=True,
            single_video=single_video,
            metadata_status="ok",
            metadata_source="yt-dlp",
            title=payload.get("title") if isinstance(payload.get("title"), str) else sample.title,
            creator=(payload.get("uploader") or payload.get("channel")) if isinstance(payload.get("uploader") or payload.get("channel"), str) else sample.creator,
            duration_sec=round(duration),
            subtitle_tracks=tracks,
            subtitle_coverage_status=subtitle_status,  # type: ignore[arg-type]
            hard_subtitle_status="unknown",
            probe_status=status,  # type: ignore[arg-type]
            stable_error_code="subtitle_body_incomplete" if status == "incomplete" else None,
            rights_status=sample.rights_status,
            rights_note=sample.rights_evidence_note,
            notes=["single_video_false" if not single_video else "public_metadata_ok"],
        )

    @staticmethod
    def _failed_record(sample: Sample, code: str) -> ProbeRecord:
        return ProbeRecord(
            sample_id=sample.sample_id,
            platform=sample.platform,
            platform_id=sample.platform_id,
            public_url=sample.public_url,
            checked_at=sample.publicly_accessible_at,
            publicly_accessible=False,
            single_video=False,
            metadata_status="failed",
            metadata_source="yt-dlp",
            probe_status="failed",
            stable_error_code=code,
            rights_status=sample.rights_status,
            rights_note=sample.rights_evidence_note,
        )


@dataclass(frozen=True)
class FixtureProbe:
    snapshot: ProbeSnapshot

    def probe_sample(self, sample: Sample) -> ProbeRecord:
        for record in self.snapshot.records:
            if record.sample_id == sample.sample_id:
                return record
        raise ProbeError("probe_sample_not_in_snapshot")


def run_formal_probe(catalog: BenchmarkCatalog, *, live: bool = False, probe: ConnectorProbe | None = None) -> ProbeSnapshot:
    """Probe all 12 samples or return the checked-in read-only projection."""

    if not live:
        return load_probe_snapshot(catalog.probe_snapshot if Path(catalog.probe_snapshot).is_absolute() else None)
    if probe is None:
        configure_trusted_ca(os.environ.get("TLS_CA_BUNDLE"))
    adapter = probe or ConnectorProbe()
    records = [adapter.probe_sample(sample) for sample in catalog.samples]
    return ProbeSnapshot(
        revision=catalog.revision,
        probe_date=catalog.freeze_date,
        probe_kind="formal_connector_probe",
        source_methods=["yt-dlp public metadata", "bounded in-memory subtitle body probe"],
        records=records,
    )


def write_probe_snapshot(snapshot: ProbeSnapshot, path: str | Path) -> None:
    """Write only normalized fields; never dump connector payloads."""

    target = Path(path)
    target.write_text(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def apply_probe_snapshot(catalog: BenchmarkCatalog, snapshot: ProbeSnapshot) -> BenchmarkCatalog:
    """Return a catalog with safe probe fields refreshed, preserving truth."""

    if snapshot.revision != catalog.revision:
        raise ProbeError("probe_revision_mismatch")
    by_id = {record.sample_id: record for record in snapshot.records}
    if set(by_id) != {sample.sample_id for sample in catalog.samples}:
        raise ProbeError("probe_catalog_identity_mismatch")
    samples: list[Sample] = []
    for sample in catalog.samples:
        record = by_id.get(sample.sample_id)
        if (
            record is None
            or record.platform != sample.platform
            or record.platform_id != sample.platform_id
            or record.public_url != sample.public_url
        ):
            raise ProbeError("probe_catalog_identity_mismatch")
        samples.append(
            Sample.model_validate(
                {
                    **sample.model_dump(mode="python"),
                    "probe_status": record.probe_status,
                    "subtitle_tracks": [item.model_dump(mode="python") for item in record.subtitle_tracks],
                    "subtitle_coverage_status": record.subtitle_coverage_status,
                    "hard_subtitle_status": record.hard_subtitle_status,
                }
            )
        )
    return BenchmarkCatalog.model_validate(
        {**catalog.model_dump(mode="python"), "samples": [sample.model_dump(mode="python") for sample in samples]}
    )
