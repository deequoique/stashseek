"""Ephemeral run directories and verifiable cleanup."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redaction import redact_report


class CleanupError(RuntimeError):
    """Raised when a run directory cannot be safely cleaned."""


_RUN_MARKER = ".video-recognition-run"
_FORBIDDEN_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".wav", ".mp3", ".m4a", ".srt", ".vtt"}


@dataclass(frozen=True)
class CleanupReport:
    run_dir: str
    existed: bool
    removed: bool
    remaining_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "run_dir_created": self.existed,
            "removed": self.removed,
            "remaining_path_count": len(self.remaining_paths),
        }


@dataclass
class TemporaryRun:
    """Own a private temporary tree and only delete the tree it created."""

    prefix: str = "video-recognition-"
    base_dir: str | Path | None = None
    path: Path | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def __enter__(self) -> "TemporaryRun":
        if self.path is not None:
            raise CleanupError("temporary run cannot be entered twice")
        root = Path(self.base_dir) if self.base_dir is not None else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=self.prefix, dir=str(root) if root else None))
        (self.path / _RUN_MARKER).write_text("owned-by-video-recognition-harness\n", encoding="utf-8")
        return self

    def _require_open(self) -> Path:
        if self.path is None or self._closed:
            raise CleanupError("temporary run is not open")
        return self.path

    def child(self, name: str) -> Path:
        root = self._require_open()
        candidate = (root / name).resolve()
        if candidate.parent != root.resolve() or candidate.name in {_RUN_MARKER, ""}:
            raise CleanupError("temporary child path escapes the run directory")
        if candidate.suffix.lower() in _FORBIDDEN_SUFFIXES:
            raise CleanupError("complete media/subtitle files are not allowed in a benchmark run")
        candidate.touch()
        return candidate

    def write_safe_json(self, name: str, payload: Any) -> Path:
        target = self.child(name)
        safe = redact_report(payload)
        target.write_text(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def cleanup(self) -> CleanupReport:
        root = self.path
        if root is None:
            return CleanupReport("", False, True, ())
        if self._closed:
            return CleanupReport(str(root), True, not root.exists(), tuple(str(path) for path in root.rglob("*")) if root.exists() else ())
        marker = root / _RUN_MARKER
        if not marker.is_file():
            raise CleanupError("refusing to delete a directory without the run marker")
        try:
            shutil.rmtree(root)
        except OSError as exc:
            raise CleanupError("benchmark temporary cleanup failed") from exc
        self._closed = True
        return CleanupReport(str(root), True, not root.exists(), ())

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()


def verify_cleanup(report: CleanupReport) -> None:
    if not report.removed or report.remaining_paths:
        raise CleanupError("temporary benchmark artifacts remain after cleanup")


def cleanup_run_directory(path: str | Path) -> CleanupReport:
    """Clean only a marked run directory, never a caller-provided arbitrary path."""

    root = Path(path).resolve()
    if not root.exists():
        return CleanupReport(str(root), False, True, ())
    if not root.is_dir() or not (root / _RUN_MARKER).is_file():
        raise CleanupError("refusing to clean an unmarked benchmark directory")
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise CleanupError("benchmark temporary cleanup failed") from exc
    return CleanupReport(str(root), True, not root.exists(), ())
