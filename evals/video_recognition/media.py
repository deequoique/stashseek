"""Bounded media planning and in-memory dry-run inputs.

The planner creates reviewable anchors only.  It does not download media or
claim that an anchor contains a gold fact; every generated plan remains
``pending_manual_review`` until an annotator replaces it with evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .adapters import AudioWindowInput, FrameBundleInput, FrameInput
from .protocol import MediaSampleBundle
from .schema import (
    BENCHMARK_REVISION,
    MediaPlanBundle,
    MediaPlanFrame,
    MediaPlanWindow,
    Sample,
    TimeRange,
)


def _anchor_start(duration_sec: int, fraction: float) -> float:
    if duration_sec <= 30:
        return 0.0
    return round(min(float(duration_sec - 30), max(0.0, duration_sec * fraction)), 2)


def build_media_plan(sample: Sample) -> tuple[list[MediaPlanWindow], list[MediaPlanBundle], list[MediaPlanFrame]]:
    """Build deterministic anchors for manual review, never as frozen truth."""

    first = _anchor_start(sample.duration_sec, 0.05)
    second = _anchor_start(sample.duration_sec, 0.60)
    audio: list[MediaPlanWindow] = []
    bundles: list[MediaPlanBundle] = []
    for index, (start, purpose) in enumerate(((first, "representative"), (second, "difficulty")), 1):
        # Stay below the hard ceiling so decimal rounding cannot turn a
        # nominal 60-second window into 60.0000000001 seconds.
        end = min(float(sample.duration_sec), start + min(59.0, max(1.0, sample.duration_sec)))
        if end <= start:
            end = start + 1.0
        window = TimeRange(start_sec=start, end_sec=end)
        audio.append(
            MediaPlanWindow(
                window_id=f"{sample.sample_id.lower()}.audio.{index}",
                purpose=purpose,
                time_range=window,
            )
        )
        frame_times = tuple(
            round(min(float(sample.duration_sec), start + offset), 2)
            for offset in (0.0, min(5.0, window.duration_sec / 3), min(10.0, window.duration_sec * 2 / 3), min(15.0, max(0.01, window.duration_sec - 0.01)))
        )
        frames = [
            MediaPlanFrame(frame_id=f"{sample.sample_id.lower()}.frame.{index}.{frame_index}", timestamp_sec=timestamp)
            for frame_index, timestamp in enumerate(dict.fromkeys(frame_times), 1)
        ]
        bundles.append(
            MediaPlanBundle(
                bundle_id=f"{sample.sample_id.lower()}.bundle.{index}",
                time_range=window,
                frames=frames,
            )
        )
    # OCR remains conditional.  The plan includes only text-likely samples,
    # and still needs an annotator to confirm each frame is worth submitting.
    text_tags = {"slides", "code", "ui", "dense-ui", "numbers"}
    ocr_candidates = sample.content_tags and text_tags.intersection(sample.content_tags)
    ocr = []
    if ocr_candidates:
        for index, timestamp in enumerate((first, second, min(float(sample.duration_sec), second + 15.0)), 1):
            ocr.append(MediaPlanFrame(frame_id=f"{sample.sample_id.lower()}.ocr.{index}", timestamp_sec=timestamp))
    return audio, bundles, ocr[:3]


def descriptor_for_sample(sample: Sample) -> MediaSampleBundle:
    if sample.media_plan_status == "frozen":
        audio, bundles, ocr = sample.audio_plan, sample.frame_plan, sample.ocr_frame_plan
    else:
        audio, bundles, ocr = build_media_plan(sample)
    return MediaSampleBundle(
        bundle_id=f"{sample.sample_id.lower()}.bundle",
        sample_id=sample.sample_id,
        revision=BENCHMARK_REVISION,
        audio_windows=[item.time_range for item in audio],
        frame_bundles=[[frame.timestamp_sec for frame in item.frames] for item in bundles],
        ocr_frame_times=[item.timestamp_sec for item in ocr],
    )


@dataclass(frozen=True)
class SyntheticInputSet:
    """Small non-media bytes used only to exercise adapter contracts."""

    audio: AudioWindowInput
    frame_bundle: FrameBundleInput
    ocr_frames: tuple[FrameInput, ...]


def synthetic_inputs(sample: Sample) -> SyntheticInputSet:
    descriptor = descriptor_for_sample(sample)
    # The digest makes each dry-run input distinct without encoding media or
    # a source URL.  It is not an evidence hash and is never written to reports.
    seed = hashlib.sha256(sample.sample_id.encode("ascii")).digest()[:12]
    audio = AudioWindowInput(
        sample_id=sample.sample_id,
        window_id=f"{sample.sample_id.lower()}.audio.1",
        time_range=descriptor.audio_windows[0],
        payload=b"dry-run-audio:" + seed,
    )
    frames = tuple(
        FrameInput(
            sample_id=sample.sample_id,
            frame_id=f"{sample.sample_id.lower()}.frame.1.{index}",
            timestamp_sec=timestamp,
            payload=b"dry-run-frame:" + seed + bytes([index]),
        )
        for index, timestamp in enumerate(descriptor.frame_bundles[0], 1)
    )
    return SyntheticInputSet(
        audio=audio,
        frame_bundle=FrameBundleInput(sample.sample_id, f"{sample.sample_id.lower()}.bundle.1", frames),
        ocr_frames=tuple(
            FrameInput(sample.sample_id, frame_id, timestamp, b"dry-run-ocr:" + seed)
            for frame_id, timestamp in (
                (f"{sample.sample_id.lower()}.ocr.{index}", timestamp)
                for index, timestamp in enumerate(descriptor.ocr_frame_times, 1)
            )
        ),
    )
