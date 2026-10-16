"""The Celery worker installs structured runtime logging via ``after_setup_logger``."""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.signals import after_setup_logger
from celery.utils.log import LoggingProxy

from app.diagnostics import (
    LOGGER,
    DailySizeRotatingFileHandler,
    shutdown_runtime_logging,
)
from app.ingest import tasks

REPO_ROOT = Path(__file__).resolve().parents[1]

ASR_DIAGNOSTIC = {
    "event": "asr_transcription",
    "provider": "fun_asr",
    "model": "fun-asr",
    "audio_seconds": 61.5,
    "billed_seconds": 62.0,
    "estimated_cost": 0.01612,
    "currency": "CNY",
    "duration_ms": 4200,
    "fallback_used": False,
    "cue_count": 12,
}


@pytest.fixture(autouse=True)
def _restore_runtime_logger():
    level, propagate = LOGGER.level, LOGGER.propagate
    shutdown_runtime_logging()
    yield
    shutdown_runtime_logging()
    LOGGER.setLevel(level)
    LOGGER.propagate = propagate


def _settings(log_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        stashseek_log_dir=str(log_dir),
        stashseek_log_max_bytes=1024 * 1024,
        stashseek_log_backup_count=2,
    )


def _send_signal() -> None:
    after_setup_logger.send(
        sender=None,
        logger=logging.getLogger(),
        loglevel=logging.INFO,
        logfile=None,
        format="",
        colorize=False,
    )


def _log_lines(log_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in sorted(log_dir.glob("notebook-agent-*.log")):
        lines.extend(line for line in path.read_text().splitlines() if line)
    return lines


def _events(lines: list[str], event: str) -> list[dict]:
    parsed = [json.loads(line) for line in lines]
    return [payload for payload in parsed if payload.get("event") == event]


def _emit_asr_diagnostic() -> None:
    # Same shape and logger as ``app.asr.transcriber``.
    logging.getLogger("notebook_agent.runtime").info(
        "diagnostic", extra={"diagnostic_payload": dict(ASR_DIAGNOSTIC)}
    )


def test_signal_receiver_is_registered_on_after_setup_logger() -> None:
    receivers = [ref() for _key, ref in after_setup_logger.receivers]
    assert tasks._install_worker_runtime_logging in receivers


def test_after_setup_logger_signal_configures_runtime_logger(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))
    assert LOGGER.handlers == []

    _send_signal()

    kinds = {type(handler) for handler in LOGGER.handlers}
    assert DailySizeRotatingFileHandler in kinds
    assert logging.StreamHandler in kinds
    assert LOGGER.level == logging.INFO
    assert LOGGER.propagate is False
    assert _events(_log_lines(tmp_path), "runtime_logging_enabled")


def test_asr_shaped_diagnostic_reaches_file_and_console_as_one_json_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))
    tasks._install_worker_runtime_logging()
    capsys.readouterr()

    _emit_asr_diagnostic()

    out_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(out_lines) == 1
    assert json.loads(out_lines[0]) == ASR_DIAGNOSTIC
    file_events = _events(_log_lines(tmp_path), "asr_transcription")
    assert file_events == [ASR_DIAGNOSTIC]


def test_completion_diagnostic_payload_is_no_longer_a_bare_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))
    tasks._install_worker_runtime_logging()
    capsys.readouterr()

    tasks._completion_diagnostic(
        "completion_event_created", event_id=7, outcome="completed", item_state="ready"
    )

    out_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(out_lines) == 1
    payload = json.loads(out_lines[0])
    assert payload["event"] == "completion_event_created"
    assert payload["event_id"] == 7
    assert payload["item_state"] == "ready"


def test_installation_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))

    tasks._install_worker_runtime_logging()
    first = list(LOGGER.handlers)
    tasks._install_worker_runtime_logging()
    _send_signal()
    _send_signal()

    assert list(LOGGER.handlers) == first
    assert len(first) == 2
    _emit_asr_diagnostic()
    assert len(_events(_log_lines(tmp_path), "asr_transcription")) == 1


def test_legacy_notebook_agent_settings_are_honoured(
    tmp_path: Path, monkeypatch
) -> None:
    legacy = SimpleNamespace(
        notebook_agent_log_dir=str(tmp_path),
        notebook_agent_log_max_bytes=1024 * 1024,
        notebook_agent_log_backup_count=2,
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: legacy)

    tasks._install_worker_runtime_logging()

    assert any(
        isinstance(handler, DailySizeRotatingFileHandler) for handler in LOGGER.handlers
    )
    assert list(tmp_path.glob("notebook-agent-*.log"))


def test_failing_configure_runtime_logging_is_swallowed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))

    def boom(**_kwargs):
        raise RuntimeError("PRIVATE-detail")

    monkeypatch.setattr(tasks, "configure_runtime_logging", boom)

    tasks._install_worker_runtime_logging()
    _send_signal()

    assert LOGGER.handlers == []


def test_failing_settings_resolution_is_swallowed(monkeypatch) -> None:
    def boom():
        raise ValueError("bad settings")

    monkeypatch.setattr(tasks, "get_settings", boom)

    tasks._install_worker_runtime_logging()
    _send_signal()

    assert LOGGER.handlers == []


def test_unwritable_log_dir_keeps_console_and_does_not_raise(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(blocker / "logs"))

    tasks._install_worker_runtime_logging()
    capsys.readouterr()
    _emit_asr_diagnostic()

    assert not any(
        isinstance(handler, DailySizeRotatingFileHandler) for handler in LOGGER.handlers
    )
    out_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert [json.loads(line) for line in out_lines] == [ASR_DIAGNOSTIC]


def test_console_handler_binds_real_stdout_not_celery_logging_proxy(
    tmp_path: Path, monkeypatch
) -> None:
    redirected = logging.getLogger("test.worker_logging.redirected")
    redirected_records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            redirected_records.append(record)

    redirected.addHandler(_Collect())
    redirected.propagate = False
    redirected.setLevel(logging.DEBUG)
    proxy = LoggingProxy(redirected, logging.WARNING)
    real = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real)
    monkeypatch.setattr(sys, "stdout", proxy)
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(tmp_path))

    tasks._install_worker_runtime_logging()

    # Celery's later redirect target is restored, not replaced.
    assert sys.stdout is proxy
    _emit_asr_diagnostic()
    assert redirected_records == []
    lines = [line for line in real.getvalue().splitlines() if line]
    assert json.loads(lines[-1]) == ASR_DIAGNOSTIC
    console = [
        handler
        for handler in LOGGER.handlers
        if type(handler) is logging.StreamHandler
    ]
    assert len(console) == 1 and console[0].stream is real


def test_importing_tasks_module_does_not_install_handlers() -> None:
    code = (
        "import logging\n"
        "import app.ingest.tasks\n"
        "logger = logging.getLogger('notebook_agent.runtime')\n"
        "assert logger.handlers == [], logger.handlers\n"
        "assert logger.propagate is True\n"
        "print('clean')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "clean"
