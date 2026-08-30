from __future__ import annotations

import pytest

from app import config
from app.config import Settings
from app import deployment


_RENAMED_KEYS = (
    "STASHSEEK_ENV",
    "NOTEBOOK_AGENT_ENV",
    "STASHSEEK_LOG_DIR",
    "NOTEBOOK_AGENT_LOG_DIR",
    "STASHSEEK_LOG_MAX_BYTES",
    "NOTEBOOK_AGENT_LOG_MAX_BYTES",
    "STASHSEEK_LOG_BACKUP_COUNT",
    "NOTEBOOK_AGENT_LOG_BACKUP_COUNT",
    "STASHSEEK_LOG_RETRIEVAL_CONTENT",
    "NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT",
)


def _clear_renamed_keys(monkeypatch):
    for key in _RENAMED_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_stashseek_configuration_is_canonical(monkeypatch):
    _clear_renamed_keys(monkeypatch)
    monkeypatch.setenv("STASHSEEK_ENV", "development")
    monkeypatch.setenv("STASHSEEK_LOG_DIR", ".runtime/stashseek-logs")
    settings = Settings(database_url="sqlite://")
    assert settings.stashseek_env == "development"
    assert settings.stashseek_log_dir == ".runtime/stashseek-logs"


def test_legacy_configuration_remains_supported(monkeypatch):
    _clear_renamed_keys(monkeypatch)
    monkeypatch.setenv("NOTEBOOK_AGENT_ENV", "development")
    monkeypatch.setenv("NOTEBOOK_AGENT_LOG_MAX_BYTES", "2048")
    settings = Settings(database_url="sqlite://")
    assert settings.stashseek_env == "development"
    assert settings.stashseek_log_max_bytes == 2048


def test_stashseek_configuration_wins_over_legacy(monkeypatch):
    _clear_renamed_keys(monkeypatch)
    monkeypatch.setenv("NOTEBOOK_AGENT_ENV", "development")
    monkeypatch.setenv("STASHSEEK_ENV", "production")
    monkeypatch.setenv("STASHSEEK_LOG_RETRIEVAL_CONTENT", "false")
    monkeypatch.setenv("BROWSER_COMPANION_ALLOWED_ORIGINS", "")
    assert Settings(database_url="sqlite://").stashseek_env == "production"


def test_process_legacy_configuration_wins_over_dotenv_canonical(monkeypatch):
    _clear_renamed_keys(monkeypatch)
    monkeypatch.setattr(config, "_DOTENV_VALUES", {"STASHSEEK_ENV": "production"})
    monkeypatch.setenv("NOTEBOOK_AGENT_ENV", "development")
    assert Settings(database_url="sqlite://").stashseek_env == "development"


def test_invalid_canonical_boolean_names_the_canonical_key(monkeypatch):
    _clear_renamed_keys(monkeypatch)
    monkeypatch.setenv("STASHSEEK_LOG_RETRIEVAL_CONTENT", "maybe")
    with pytest.raises(ValueError, match="STASHSEEK_LOG_RETRIEVAL_CONTENT"):
        Settings(database_url="sqlite://")


def test_launcher_mapping_uses_canonical_value_first():
    assert deployment._compat_value(
        {"STASHSEEK_PROFILE": "read", "NOTEBOOK_AGENT_PROFILE": "full"},
        "STASHSEEK_PROFILE",
        "NOTEBOOK_AGENT_PROFILE",
    ) == "read"
    assert deployment._compat_value(
        {"NOTEBOOK_AGENT_PROFILE": "full"},
        "STASHSEEK_PROFILE",
        "NOTEBOOK_AGENT_PROFILE",
    ) == "full"


def test_launcher_preserves_source_precedence_across_compat_names(tmp_path):
    managed = tmp_path / ".env.runtime"
    operator = tmp_path / ".env"
    managed.write_text("STASHSEEK_PROFILE=full\n", encoding="utf-8")
    operator.write_text("STASHSEEK_PROFILE=langbot\n", encoding="utf-8")

    resolved = deployment.load_environment(
        {"NOTEBOOK_AGENT_PROFILE": "read"},
        managed_path=managed,
        operator_path=operator,
    )

    assert resolved["STASHSEEK_PROFILE"] == "read"
