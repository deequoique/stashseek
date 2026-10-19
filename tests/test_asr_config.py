"""ASR settings: safe defaults and validation only when ASR is enabled."""

import pytest

from app.config import Settings

ASR_ENV_NAMES = (
    "ASR_ENABLED",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "FUN_ASR_MODEL",
    "OPENAI_API_KEY",
    "ASR_WHISPER_FALLBACK_ENABLED",
    "ASR_PUBLIC_BASE_URL",
    "ASR_AUDIO_URL_SECRET",
    "ASR_AUDIO_URL_TTL_SECONDS",
    "ASR_MAX_DURATION_SECONDS",
    "ASR_MAX_AUDIO_BYTES",
    "ASR_DOWNLOAD_TIMEOUT_SECONDS",
    "ASR_PROVIDER_TIMEOUT_SECONDS",
    "ASR_POLL_INTERVAL_SECONDS",
)
SECRET = "s" * 32
API_KEY_SENTINEL = "sk-dashscope-sentinel-value"


@pytest.fixture(autouse=True)
def clean_asr_env(monkeypatch):
    for name in ASR_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Settings() only builds a URL string; nothing here connects to a database.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/unused")


def enable(monkeypatch, **overrides):
    values = {
        "ASR_ENABLED": "true",
        "DASHSCOPE_API_KEY": API_KEY_SENTINEL,
        "ASR_PUBLIC_BASE_URL": "https://stashseek.example.test",
        "ASR_AUDIO_URL_SECRET": SECRET,
    }
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_defaults_are_disabled_and_need_no_asr_configuration():
    settings = Settings()

    assert settings.asr_enabled is False
    assert settings.dashscope_api_key is None
    assert settings.dashscope_base_url == "https://dashscope-intl.aliyuncs.com"
    assert settings.fun_asr_model == "fun-asr"
    assert settings.openai_api_key is None
    assert settings.asr_whisper_fallback_enabled is True
    assert settings.asr_public_base_url is None
    assert settings.asr_audio_url_secret is None
    assert settings.asr_audio_url_ttl_seconds == 7200
    assert settings.asr_max_duration_seconds == 14400
    assert settings.asr_max_audio_bytes == 524_288_000
    assert settings.asr_download_timeout_seconds == 900
    assert settings.asr_provider_timeout_seconds == 1800
    assert settings.asr_poll_interval_seconds == 5.0


def test_enabled_with_required_values_loads_every_field(monkeypatch):
    enable(
        monkeypatch,
        DASHSCOPE_BASE_URL="https://dashscope.example.test/proxy",
        FUN_ASR_MODEL="fun-asr-mtl",
        OPENAI_API_KEY="sk-openai",
        ASR_WHISPER_FALLBACK_ENABLED="false",
        ASR_AUDIO_URL_TTL_SECONDS="3600",
        ASR_MAX_DURATION_SECONDS="7200",
        ASR_MAX_AUDIO_BYTES="1048576",
        ASR_DOWNLOAD_TIMEOUT_SECONDS="60",
        ASR_PROVIDER_TIMEOUT_SECONDS="120",
        ASR_POLL_INTERVAL_SECONDS="2.5",
    )

    settings = Settings()

    assert settings.asr_enabled is True
    assert settings.dashscope_api_key == API_KEY_SENTINEL
    assert settings.dashscope_base_url == "https://dashscope.example.test/proxy"
    assert settings.fun_asr_model == "fun-asr-mtl"
    assert settings.openai_api_key == "sk-openai"
    assert settings.asr_whisper_fallback_enabled is False
    assert settings.asr_public_base_url == "https://stashseek.example.test"
    assert settings.asr_audio_url_secret == SECRET
    assert settings.asr_audio_url_ttl_seconds == 3600
    assert settings.asr_max_duration_seconds == 7200
    assert settings.asr_max_audio_bytes == 1_048_576
    assert settings.asr_download_timeout_seconds == 60
    assert settings.asr_provider_timeout_seconds == 120
    assert settings.asr_poll_interval_seconds == 2.5


def test_openai_key_is_optional_when_enabled(monkeypatch):
    enable(monkeypatch)

    assert Settings().openai_api_key is None


def test_disabled_ignores_invalid_asr_values(monkeypatch):
    monkeypatch.setenv("ASR_ENABLED", "false")
    monkeypatch.setenv("ASR_PUBLIC_BASE_URL", "http://bad.example.test/")
    monkeypatch.setenv("ASR_AUDIO_URL_SECRET", "short")
    monkeypatch.setenv("ASR_AUDIO_URL_TTL_SECONDS", "1")
    monkeypatch.setenv("ASR_POLL_INTERVAL_SECONDS", "0")

    assert Settings().asr_enabled is False


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"DASHSCOPE_API_KEY": None}, "DASHSCOPE_API_KEY"),
        ({"DASHSCOPE_API_KEY": "   "}, "DASHSCOPE_API_KEY"),
        ({"ASR_PUBLIC_BASE_URL": None}, "ASR_PUBLIC_BASE_URL"),
        ({"ASR_AUDIO_URL_SECRET": None}, "ASR_AUDIO_URL_SECRET"),
        ({"ASR_AUDIO_URL_SECRET": "s" * 31}, "ASR_AUDIO_URL_SECRET"),
        ({"FUN_ASR_MODEL": "  "}, "FUN_ASR_MODEL"),
    ],
)
def test_enabled_requires_provider_and_signing_values(monkeypatch, overrides, fragment):
    enable(monkeypatch, **overrides)

    with pytest.raises(ValueError, match=fragment) as error:
        Settings()
    assert API_KEY_SENTINEL not in str(error.value)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://stashseek.example.test",
        "https://stashseek.example.test/",
        "https://stashseek.example.test/base",
        "https://stashseek.example.test?x=1",
        "https://stashseek.example.test#frag",
        "https://user:pw@stashseek.example.test",
        "stashseek.example.test",
        "https://",
    ],
)
def test_public_base_url_must_be_a_bare_https_origin(monkeypatch, value):
    enable(monkeypatch, ASR_PUBLIC_BASE_URL=value)

    with pytest.raises(ValueError, match="ASR_PUBLIC_BASE_URL") as error:
        Settings()
    assert "pw" not in str(error.value)


def test_public_base_url_accepts_explicit_port(monkeypatch):
    enable(monkeypatch, ASR_PUBLIC_BASE_URL="https://stashseek.example.test:8443")

    assert Settings().asr_public_base_url == "https://stashseek.example.test:8443"


@pytest.mark.parametrize(
    "value",
    [
        "http://dashscope-intl.aliyuncs.com",
        "https://dashscope-intl.aliyuncs.com/",
        "https://user:pw@dashscope-intl.aliyuncs.com",
        "https://dashscope-intl.aliyuncs.com?x=1",
    ],
)
def test_dashscope_base_url_must_be_https_without_trailing_slash(monkeypatch, value):
    enable(monkeypatch, DASHSCOPE_BASE_URL=value)

    with pytest.raises(ValueError, match="DASHSCOPE_BASE_URL"):
        Settings()


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [
        ("ASR_AUDIO_URL_TTL_SECONDS", 300, 86_400),
        ("ASR_MAX_DURATION_SECONDS", 60, 43_200),
        ("ASR_MAX_AUDIO_BYTES", 1_048_576, 2_147_483_648),
        ("ASR_DOWNLOAD_TIMEOUT_SECONDS", 30, 3_000),
        ("ASR_PROVIDER_TIMEOUT_SECONDS", 60, 3_000),
    ],
)
def test_integer_ranges_are_inclusive(monkeypatch, name, low, high):
    for accepted in (low, high):
        enable(monkeypatch, **{name: str(accepted)})
        assert getattr(Settings(), name.lower()) == accepted
    for rejected in (low - 1, high + 1):
        enable(monkeypatch, **{name: str(rejected)})
        with pytest.raises(ValueError, match=name):
            Settings()


@pytest.mark.parametrize("value", ["0.99", "60.01", "nan", "inf", "-1"])
def test_poll_interval_must_be_finite_and_between_one_and_sixty(monkeypatch, value):
    enable(monkeypatch, ASR_POLL_INTERVAL_SECONDS=value)

    with pytest.raises(ValueError, match="ASR_POLL_INTERVAL_SECONDS"):
        Settings()


@pytest.mark.parametrize("value", ["1", "60", "5.5"])
def test_poll_interval_accepts_bounds(monkeypatch, value):
    enable(monkeypatch, ASR_POLL_INTERVAL_SECONDS=value)

    assert Settings().asr_poll_interval_seconds == float(value)
