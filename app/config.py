"""Runtime configuration.

All values come from environment variables (optionally loaded from a local
`.env` file via python-dotenv). Nothing here is hardcoded — see
`.env.example` for the full list of variables and their defaults for local
docker-compose usage.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
import ipaddress
import math
from urllib.parse import urlparse, urlsplit

from dotenv import dotenv_values, load_dotenv

# Renamed keys need source-aware precedence: process canonical, process legacy,
# .env canonical, .env legacy. python-dotenv normally flattens process and .env
# into os.environ, so retain renamed .env values separately and remove only the
# values that load_dotenv inserted for those keys.
_RENAMED_ENV_PAIRS: tuple[tuple[str, str], ...] = (
    ("STASHSEEK_ENV", "NOTEBOOK_AGENT_ENV"),
    ("STASHSEEK_LOG_DIR", "NOTEBOOK_AGENT_LOG_DIR"),
    ("STASHSEEK_LOG_MAX_BYTES", "NOTEBOOK_AGENT_LOG_MAX_BYTES"),
    ("STASHSEEK_LOG_BACKUP_COUNT", "NOTEBOOK_AGENT_LOG_BACKUP_COUNT"),
    ("STASHSEEK_LOG_RETRIEVAL_CONTENT", "NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT"),
)
_PROCESS_ENV_AT_IMPORT = frozenset(os.environ)
_DOTENV_VALUES = {
    key: value
    for key, value in dotenv_values().items()
    if value is not None
}
# Load all ordinary values once. Production process values still win because
# load_dotenv does not override them.
load_dotenv()
for _canonical, _legacy in _RENAMED_ENV_PAIRS:
    for _key in (_canonical, _legacy):
        if _key not in _PROCESS_ENV_AT_IMPORT:
            os.environ.pop(_key, None)


# The answer Composer allows one structured-output repair in a run. Keep this
# constant next to configuration validation so the provider cap and the
# post-response safety budget cannot drift apart.
COMPOSER_VALIDATION_REQUEST_LIMIT = 1

# The public privacy policy page shows these two settings, so their defaults
# live here and the page falls back to them when no Settings object is wired.
DEFAULT_PRIVACY_CONTACT_EMAIL = "deequoique@gmail.com"
DEFAULT_TRASH_RETENTION_DAYS = 30
_PLAUSIBLE_EMAIL = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _compat_env(
    canonical: str,
    legacy: str,
    default: str | None = None,
) -> str | None:
    """Read a renamed setting with deterministic canonical precedence.

    The canonical StashSeek key wins even when it is set to an empty value;
    this mirrors normal process-environment semantics and makes an operator's
    explicit override predictable during a gradual migration.
    """

    if canonical in os.environ:
        return os.environ[canonical]
    if legacy in os.environ:
        return os.environ[legacy]
    if canonical in _DOTENV_VALUES:
        return _DOTENV_VALUES[canonical]
    return _DOTENV_VALUES.get(legacy, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _compat_env_int(canonical: str, legacy: str, default: int) -> int:
    value = _compat_env(canonical, legacy)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class _WemmEnv:
    """Defer optional provider parsing until constructor flags are known."""

    name: str
    default: str | int | bool

    def resolve(self, *, enabled: bool):
        if not enabled:
            return self.default
        if isinstance(self.default, bool):
            return _env_bool(self.name, self.default)
        if isinstance(self.default, int):
            return _env_int(self.name, self.default)
        return _env(self.name, self.default)


def _compat_env_bool(canonical: str, legacy: str, default: bool) -> bool:
    value = _compat_env(canonical, legacy)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{canonical} must be a boolean")


def _env_channels(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    channels = tuple(
        dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip())
    )
    unsupported = set(channels) - {"telegram", "wechat"}
    if not channels or unsupported:
        raise ValueError(f"{name} must contain only telegram and/or wechat")
    return channels


def _env_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    return tuple(
        dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
    )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _validate_langbot_outbound_url(value: str) -> None:
    """Fail closed for an unsafe LangBot outbound endpoint."""

    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must not contain credentials, query, or fragment"
        )
    host = parsed.hostname.lower().rstrip(".")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and parsed.scheme != "https":
        raise ValueError(
            "LANGBOT_OUTBOUND_BASE_URL must use HTTPS for non-loopback hosts"
        )


def _validate_us_video_proxy_url(value: str | None) -> None:
    """Restrict the US video egress proxy to a credential-free loopback URL."""

    if value is None:
        return
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(
            "US_VIDEO_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        )
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "US_VIDEO_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path
        or "?" in value
        or "#" in value
    ):
        raise ValueError(
            "US_VIDEO_PROXY_URL must be a credential-free loopback HTTP URL "
            "with an explicit port"
        )


def _validate_asr_https_url(name: str, value: str | None, *, allow_path: bool) -> None:
    """Validate an ASR HTTPS base URL without echoing its value."""

    message = (
        f"{name} must be an https URL without credentials, query, fragment, "
        "or trailing slash"
        + ("" if allow_path else " and without a path")
    )
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(message)
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or value.endswith("/")
        or (not allow_path and parsed.path)
    ):
        raise ValueError(message)


@dataclass(frozen=True)
class Settings:
    # --- Private runtime diagnostics ---
    # The relative default intentionally resolves in the gateway working
    # directory. Production systemd sets this to /var/log/notebook-agent.
    notebook_agent_log_dir: str = field(
        default_factory=lambda: _compat_env(
            "STASHSEEK_LOG_DIR", "NOTEBOOK_AGENT_LOG_DIR", ".runtime/logs"
        )
        or ".runtime/logs"
    )
    notebook_agent_log_max_bytes: int = field(
        default_factory=lambda: _compat_env_int(
            "STASHSEEK_LOG_MAX_BYTES", "NOTEBOOK_AGENT_LOG_MAX_BYTES", 10 * 1024 * 1024
        )
    )
    notebook_agent_log_backup_count: int = field(
        default_factory=lambda: _compat_env_int(
            "STASHSEEK_LOG_BACKUP_COUNT", "NOTEBOOK_AGENT_LOG_BACKUP_COUNT", 5
        )
    )
    notebook_agent_env: str = field(
        default_factory=lambda: _compat_env(
            "STASHSEEK_ENV", "NOTEBOOK_AGENT_ENV", "production"
        )
        or "production"
    )
    notebook_agent_log_retrieval_content: bool = field(
        default_factory=lambda: _compat_env_bool(
            "STASHSEEK_LOG_RETRIEVAL_CONTENT",
            "NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT",
            False,
        )
    )

    # Canonical StashSeek-named accessors.  The dataclass fields above retain
    # their constructor names for Python callers during the compatibility
    # window; runtime code uses these accessors so new code does not spread
    # the former product name further.
    @property
    def stashseek_log_dir(self) -> str:
        return self.notebook_agent_log_dir

    @property
    def stashseek_log_max_bytes(self) -> int:
        return self.notebook_agent_log_max_bytes

    @property
    def stashseek_log_backup_count(self) -> int:
        return self.notebook_agent_log_backup_count

    @property
    def stashseek_env(self) -> str:
        return self.notebook_agent_env

    @property
    def stashseek_log_retrieval_content(self) -> bool:
        return self.notebook_agent_log_retrieval_content
    # --- Outbound TLS ---
    # Optional explicit CA bundle.  If unset, application composition uses
    # SSL_CERT_FILE/REQUESTS_CA_BUNDLE or certifi for the current interpreter.
    tls_ca_bundle: str | None = field(default_factory=lambda: _env("TLS_CA_BUNDLE"))

    # --- Zhipu Embedding-3 ---
    zhipu_api_key: str | None = field(default_factory=lambda: _env("ZHIPU_API_KEY"))
    embedding_model: str = field(
        default_factory=lambda: _env("EMBEDDING_MODEL", "embedding-3")
        or "embedding-3"
    )
    embedding_endpoint: str = field(
        default_factory=lambda: _env(
            "EMBEDDING_ENDPOINT",
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
        )
        or "https://open.bigmodel.cn/api/paas/v4/embeddings"
    )
    embedding_dimensions: int = field(
        default_factory=lambda: _env_int("EMBEDDING_DIMENSIONS", 1536)
    )
    embedding_batch_size: int = field(
        default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 64)
    )

    # --- Optional local WeMM multimodal embedding ---
    # Disabled by default so existing text ingestion/retrieval is unchanged.
    wemm_enabled: bool = field(default_factory=lambda: _WemmEnv("WEMM_ENABLED", False))
    wemm_model: str = field(
        default_factory=lambda: _WemmEnv(
            "WEMM_MODEL", "tencent/WeMM-Embedding-2B"
        )
    )
    wemm_revision: str | None = field(
        default_factory=lambda: _WemmEnv(
            "WEMM_REVISION",
            "bbd6cd4bf52cfc6716f752a2df80b2706720bd95",
        )
    )
    wemm_dimensions: int = field(
        default_factory=lambda: _WemmEnv("WEMM_DIMENSIONS", 1024)
    )
    wemm_device: str = field(
        default_factory=lambda: _WemmEnv("WEMM_DEVICE", "cpu")
    )
    wemm_batch_size: int = field(
        default_factory=lambda: _WemmEnv("WEMM_BATCH_SIZE", 1)
    )
    wemm_max_inputs: int = field(
        default_factory=lambda: _WemmEnv("WEMM_MAX_INPUTS", 32)
    )
    wemm_max_text_chars: int = field(
        default_factory=lambda: _WemmEnv("WEMM_MAX_TEXT_CHARS", 8_192)
    )
    wemm_max_media_bytes: int = field(
        default_factory=lambda: _WemmEnv("WEMM_MAX_MEDIA_BYTES", 128 * 1024 * 1024)
    )
    multimodal_retrieval_enabled: bool = field(
        default_factory=lambda: _env_bool("MULTIMODAL_RETRIEVAL_ENABLED", False)
    )

    # --- Postgres / SQLAlchemy ---
    database_url: str = field(default_factory=lambda: _build_database_url())

    # --- Redis (Celery broker + result backend) ---
    redis_url: str = field(default_factory=lambda: _build_redis_url())
    # The channel/Agent request must never wait indefinitely for the broker.
    # This is a total publish budget; the ingestion worker's retry policy is
    # intentionally separate and is not controlled by these values.
    broker_publish_timeout_seconds: float = field(
        default_factory=lambda: _env_float("BROKER_PUBLISH_TIMEOUT_SECONDS", 5.0)
    )
    broker_publish_max_retries: int = field(
        default_factory=lambda: _env_int("BROKER_PUBLISH_MAX_RETRIES", 1)
    )
    # Tenant-level cost guardrails apply to both Web and channel saves.  They
    # bound durable queue work and long-term storage; per-request batch limits
    # alone are not sufficient because a caller can submit many batches.
    ingest_max_active_per_user: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ACTIVE_PER_USER", 10)
    )
    ingest_daily_new_item_limit: int = field(
        default_factory=lambda: _env_int("INGEST_DAILY_NEW_ITEM_LIMIT", 50)
    )
    ingest_max_items_per_user: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ITEMS_PER_USER", 1000)
    )
    ingest_max_active_global: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_ACTIVE_GLOBAL", 100)
    )
    ingest_daily_new_item_limit_global: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL", 300
        )
    )
    ingest_daily_dispatch_limit_per_user: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_DISPATCH_LIMIT_PER_USER", 100
        )
    )
    ingest_daily_dispatch_limit_global: int = field(
        default_factory=lambda: _env_int(
            "INGEST_DAILY_DISPATCH_LIMIT_GLOBAL", 1000
        )
    )
    ingest_max_raw_transcript_bytes: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_RAW_TRANSCRIPT_BYTES", 5_000_000)
    )
    ingest_max_cues_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_CUES_PER_ITEM", 50_000)
    )
    ingest_max_text_chars_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_TEXT_CHARS_PER_ITEM", 1_000_000)
    )
    ingest_max_segments_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_SEGMENTS_PER_ITEM", 5_000)
    )
    ingest_max_embedding_chars_per_item: int = field(
        default_factory=lambda: _env_int("INGEST_MAX_EMBEDDING_CHARS_PER_ITEM", 2_000_000)
    )
    youtube_fetch_timeout_seconds: float = field(
        default_factory=lambda: _env_float("YOUTUBE_FETCH_TIMEOUT_SECONDS", 30.0)
    )
    us_video_proxy_url: str | None = field(
        default_factory=lambda: _env("US_VIDEO_PROXY_URL")
    )
    bilibili_fetch_timeout_seconds: float = field(
        default_factory=lambda: _env_float("BILIBILI_FETCH_TIMEOUT_SECONDS", 30.0)
    )
    # Public-favorites and multipart access requires server egress to
    # api.bilibili.com, which datacenter IPs cannot rely on. Off unless a
    # deployment has a working egress path.
    bilibili_public_enabled: bool = field(
        default_factory=lambda: _env_bool("BILIBILI_PUBLIC_ENABLED", False)
    )

    # --- Hosted ASR for subtitle-less public videos ---
    # Disabled by default. Required values and ranges are validated only when
    # ASR_ENABLED=true so existing deployments need none of these variables.
    asr_enabled: bool = field(default_factory=lambda: _env_bool("ASR_ENABLED", False))
    dashscope_api_key: str | None = field(
        default_factory=lambda: _env("DASHSCOPE_API_KEY")
    )
    dashscope_base_url: str = field(
        default_factory=lambda: _env(
            "DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com"
        )
        or "https://dashscope-intl.aliyuncs.com"
    )
    fun_asr_model: str = field(
        default_factory=lambda: _env("FUN_ASR_MODEL", "fun-asr") or "fun-asr"
    )
    # Optional whisper-1 fallback; absent means no fallback is attempted.
    openai_api_key: str | None = field(
        default_factory=lambda: _env("OPENAI_API_KEY")
    )
    asr_whisper_fallback_enabled: bool = field(
        default_factory=lambda: _env_bool("ASR_WHISPER_FALLBACK_ENABLED", True)
    )
    asr_public_base_url: str | None = field(
        default_factory=lambda: _env("ASR_PUBLIC_BASE_URL")
    )
    asr_audio_url_secret: str | None = field(
        default_factory=lambda: _env("ASR_AUDIO_URL_SECRET")
    )
    asr_audio_url_ttl_seconds: int = field(
        default_factory=lambda: _env_int("ASR_AUDIO_URL_TTL_SECONDS", 7200)
    )
    asr_max_duration_seconds: int = field(
        default_factory=lambda: _env_int("ASR_MAX_DURATION_SECONDS", 14400)
    )
    asr_max_audio_bytes: int = field(
        default_factory=lambda: _env_int("ASR_MAX_AUDIO_BYTES", 524_288_000)
    )
    asr_download_timeout_seconds: int = field(
        default_factory=lambda: _env_int("ASR_DOWNLOAD_TIMEOUT_SECONDS", 900)
    )
    asr_provider_timeout_seconds: int = field(
        default_factory=lambda: _env_int("ASR_PROVIDER_TIMEOUT_SECONDS", 1800)
    )
    asr_poll_interval_seconds: float = field(
        default_factory=lambda: _env_float("ASR_POLL_INTERVAL_SECONDS", 5.0)
    )

    # --- MinIO (S3-compatible object storage) ---
    minio_endpoint_url: str = field(default_factory=lambda: _env("MINIO_ENDPOINT_URL", "http://localhost:9000"))
    minio_access_key: str | None = field(default_factory=lambda: _env("MINIO_ROOT_USER"))
    minio_secret_key: str | None = field(default_factory=lambda: _env("MINIO_ROOT_PASSWORD"))
    minio_bucket: str = field(default_factory=lambda: _env("MINIO_BUCKET", "kb-raw") or "kb-raw")

    # --- Knowledge retrieval Agent ---
    agent_model: str = field(
        default_factory=lambda: _env("AGENT_MODEL", "openai:gpt-5-mini")
        or "openai:gpt-5-mini"
    )
    agent_api_key: str | None = field(default_factory=lambda: _env("AGENT_API_KEY"))
    agent_base_url: str | None = field(default_factory=lambda: _env("AGENT_BASE_URL"))
    agent_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AGENT_TIMEOUT_SECONDS", 45.0)
    )
    agent_tool_timeout_seconds: float = field(
        default_factory=lambda: _env_float(
            "AGENT_TOOL_TIMEOUT_SECONDS", 15.0
        )
    )
    agent_request_limit: int = field(
        default_factory=lambda: _env_int("AGENT_REQUEST_LIMIT", 8)
    )
    agent_tool_calls_limit: int = field(
        default_factory=lambda: _env_int("AGENT_TOOL_CALLS_LIMIT", 10)
    )
    agent_output_token_limit: int = field(
        default_factory=lambda: _env_int("AGENT_OUTPUT_TOKEN_LIMIT", 3000)
    )
    agent_composer_max_tokens: int = field(
        default_factory=lambda: _env_int("AGENT_COMPOSER_MAX_TOKENS", 1000)
    )
    # Browser Agent responses use the additive SSE endpoint by default.  The
    # existing JSON endpoint remains available for clients that do not opt in
    # to streaming or when operators explicitly disable it.
    agent_streaming_enabled: bool = field(
        default_factory=lambda: _env_bool("AGENT_STREAMING_ENABLED", True)
    )
    trash_retention_days: int = field(
        default_factory=lambda: _env_int(
            "TRASH_RETENTION_DAYS", DEFAULT_TRASH_RETENTION_DAYS
        )
    )
    # Shown on the public /privacy page as the way to ask for account deletion
    # or data export. Never used for sending mail.
    privacy_contact_email: str = field(
        default_factory=lambda: (
            _env("PRIVACY_CONTACT_EMAIL", DEFAULT_PRIVACY_CONTACT_EMAIL) or ""
        ).strip()
    )
    # Once a day by default: every purge sweep is a PostgreSQL visit, and an
    # idle Neon compute can only scale to zero between visits.
    trash_purge_interval_seconds: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_INTERVAL_SECONDS", 86400)
    )
    trash_purge_batch_size: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_BATCH_SIZE", 20)
    )
    trash_purge_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int("TRASH_PURGE_CLAIM_TIMEOUT_SECONDS", 1800)
    )
    trash_purge_max_duration_seconds: float = field(
        default_factory=lambda: _env_float("TRASH_PURGE_MAX_DURATION_SECONDS", 30.0)
    )
    trash_purge_object_timeout_seconds: float = field(
        default_factory=lambda: _env_float("TRASH_PURGE_OBJECT_TIMEOUT_SECONDS", 10.0)
    )
    # Ingestion completion outbox repair is deliberately separate from the
    # recycle-bin purge sweep.  Both run on the maintenance queue, but the
    # completion settings have their own bounded claim and wall-clock budget.
    ingest_completion_interval_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_COMPLETION_INTERVAL_SECONDS",
            _env_int("INGEST_COMPLETION_PUBLISH_INTERVAL_SECONDS", 60),
        )
    )
    ingest_completion_batch_size: int = field(
        default_factory=lambda: _env_int("INGEST_COMPLETION_BATCH_SIZE", 20)
    )
    ingest_completion_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS", 300
        )
    )
    ingest_completion_max_duration_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_COMPLETION_MAX_DURATION_SECONDS", 30.0
        )
    )
    # PostgreSQL-backed source-channel completion notification poller.  The
    # legacy ingest-completion publisher settings above remain for rollback
    # compatibility but are no longer scheduled by the worker.
    ingest_notification_interval_seconds: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_INTERVAL_SECONDS", 600)
    )
    # The 600-second tick above is gated by a Redis repair index and normally
    # issues no PostgreSQL statement.  This ungated full repair rebuilds that
    # index from PostgreSQL and is the bound on a lost-Redis notification delay.
    ingest_notification_full_sweep_interval_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS", 86400
        )
    )
    ingest_notification_batch_size: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_BATCH_SIZE", 20)
    )
    ingest_notification_claim_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            "INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS", 300
        )
    )
    ingest_notification_max_duration_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_MAX_DURATION_SECONDS", 8.0
        )
    )
    ingest_notification_max_attempts: int = field(
        default_factory=lambda: _env_int("INGEST_NOTIFICATION_MAX_ATTEMPTS", 5)
    )
    ingest_notification_retry_base_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_RETRY_BASE_SECONDS", 5.0
        )
    )
    ingest_notification_retry_max_seconds: float = field(
        default_factory=lambda: _env_float(
            "INGEST_NOTIFICATION_RETRY_MAX_SECONDS", 300.0
        )
    )
    langbot_outbound_base_url: str = field(
        default_factory=lambda: _env(
            "LANGBOT_OUTBOUND_BASE_URL", "http://127.0.0.1:5300"
        )
        or "http://127.0.0.1:5300"
    )
    langbot_outbound_api_key: str | None = field(
        default_factory=lambda: _env("LANGBOT_OUTBOUND_API_KEY")
    )
    langbot_outbound_timeout_seconds: float = field(
        default_factory=lambda: _env_float("LANGBOT_OUTBOUND_TIMEOUT_SECONDS", 10.0)
    )
    context_max_turns: int = field(
        default_factory=lambda: _env_int("CONTEXT_MAX_TURNS", 8)
    )
    context_token_budget: int = field(
        default_factory=lambda: _env_int("CONTEXT_TOKEN_BUDGET", 6000)
    )
    channel_link_ttl_seconds: int = field(
        default_factory=lambda: _env_int("CHANNEL_LINK_TTL_SECONDS", 600)
    )
    channel_gateway_secret: str | None = field(
        default_factory=lambda: _env("CHANNEL_GATEWAY_SECRET")
    )
    channel_gateway_host: str = field(
        default_factory=lambda: _env("CHANNEL_GATEWAY_HOST", "127.0.0.1")
        or "127.0.0.1"
    )
    channel_gateway_port: int = field(
        default_factory=lambda: _env_int("CHANNEL_GATEWAY_PORT", 8765)
    )

    # --- MCP transport ---
    # These settings are intentionally independent from the private LangBot
    # bridge.  MCP startup never requires CHANNEL_GATEWAY_SECRET.
    mcp_host: str = field(
        default_factory=lambda: _env("MCP_HOST", "127.0.0.1") or "127.0.0.1"
    )
    mcp_port: int = field(default_factory=lambda: _env_int("MCP_PORT", 8000))
    mcp_path: str = field(
        default_factory=lambda: _env("MCP_PATH", "/mcp") or "/mcp"
    )
    mcp_url_token_mode: bool = field(
        default_factory=lambda: _env_bool("MCP_URL_TOKEN_MODE", False)
    )

    # --- Same-origin Web auth ---
    web_auth_secret: str | None = field(
        default_factory=lambda: _env("WEB_AUTH_SECRET")
    )
    web_auth_challenge_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_CHALLENGE_TTL_SECONDS", 600)
    )
    web_auth_session_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_SESSION_TTL_SECONDS", 2592000)
    )
    web_auth_attempt_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_ATTEMPT_LIMIT", 5)
    )
    web_auth_rate_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_RATE_WINDOW_SECONDS", 60)
    )
    web_auth_rate_limit_per_requester: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_RATE_LIMIT_PER_REQUESTER", 5
        )
    )
    web_auth_global_rate_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_GLOBAL_RATE_LIMIT", 100)
    )
    web_auth_active_challenge_limit: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_ACTIVE_CHALLENGE_LIMIT", 3)
    )
    web_auth_challenge_retention_seconds: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_CHALLENGE_RETENTION_SECONDS", 86400
        )
    )
    web_auth_session_retention_seconds: int = field(
        default_factory=lambda: _env_int(
            "WEB_AUTH_SESSION_RETENTION_SECONDS", 604800
        )
    )
    web_cookie_secure: bool = field(
        default_factory=lambda: _env_bool("WEB_COOKIE_SECURE", True)
    )
    web_origin: str | None = field(default_factory=lambda: _env("WEB_ORIGIN"))
    web_login_channels: tuple[str, ...] = field(
        default_factory=lambda: _env_channels(
            "WEB_LOGIN_CHANNELS", ("telegram", "wechat")
        )
    )
    web_host: str = field(
        default_factory=lambda: _env("WEB_HOST", "127.0.0.1") or "127.0.0.1"
    )
    web_port: int = field(
        default_factory=lambda: _env_int("WEB_PORT", 8000)
    )
    web_serve_static: bool = field(
        default_factory=lambda: _env_bool("WEB_SERVE_STATIC", True)
    )
    web_static_dir: str = field(
        # Retained for config-file compatibility; API-only runtime never reads it.
        default_factory=lambda: _env("WEB_STATIC_DIR", "extension/dist") or "extension/dist"
    )
    web_publish_budget_seconds: float = field(
        default_factory=lambda: _env_float("WEB_PUBLISH_BUDGET_SECONDS", 5.0)
    )
    web_forwarded_allow_ips: str = field(
        default_factory=lambda: _env("WEB_FORWARDED_ALLOW_IPS", "127.0.0.1")
        or "127.0.0.1"
    )

    # --- Same-origin Web API / email login ---
    # Disabled by default so existing channel-only deployments do not acquire
    # an email-provider dependency merely by upgrading the application.
    web_auth_enabled: bool = field(
        default_factory=lambda: _env_bool("WEB_AUTH_ENABLED", False)
    )
    web_api_prefix: str = field(
        default_factory=lambda: _env("WEB_API_PREFIX", "/api/v1") or "/api/v1"
    )
    web_public_origin: str | None = field(
        default_factory=lambda: _env("WEB_PUBLIC_ORIGIN")
    )
    web_microsoft_enabled: bool = field(default_factory=lambda: _env_bool("WEB_MICROSOFT_ENABLED", False))
    web_microsoft_client_id: str | None = field(default_factory=lambda: _env("WEB_MICROSOFT_CLIENT_ID"))
    web_microsoft_client_secret: str | None = field(default_factory=lambda: _env("WEB_MICROSOFT_CLIENT_SECRET"), repr=False)
    web_microsoft_tenant: str = field(default_factory=lambda: _env("WEB_MICROSOFT_TENANT", "common") or "common")
    web_wechat_website_enabled: bool = field(default_factory=lambda: _env_bool("WEB_WECHAT_WEBSITE_ENABLED", False))
    web_wechat_website_app_id: str | None = field(default_factory=lambda: _env("WEB_WECHAT_WEBSITE_APP_ID"))
    web_wechat_website_app_secret: str | None = field(default_factory=lambda: _env("WEB_WECHAT_WEBSITE_APP_SECRET"), repr=False)
    web_qq_enabled: bool = field(default_factory=lambda: _env_bool("WEB_QQ_ENABLED", False))
    web_qq_app_id: str | None = field(default_factory=lambda: _env("WEB_QQ_APP_ID"))
    web_qq_app_key: str | None = field(default_factory=lambda: _env("WEB_QQ_APP_KEY"), repr=False)
    browser_companion_allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_csv("BROWSER_COMPANION_ALLOWED_ORIGINS")
    )
    browser_companion_pairing_ttl_seconds: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_PAIRING_TTL_SECONDS", 600
        )
    )
    browser_companion_grant_ttl_seconds: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_GRANT_TTL_SECONDS", 90 * 24 * 60 * 60
        )
    )
    browser_companion_max_request_bytes: int = field(
        default_factory=lambda: _env_int(
            "BROWSER_COMPANION_MAX_REQUEST_BYTES", 5_500_000
        )
    )
    # --- Instant page Q&A (extension side panel; transcript is never stored) ---
    page_qa_enabled: bool = field(
        default_factory=lambda: _env_bool("PAGE_QA_ENABLED", True)
    )
    page_qa_max_request_bytes: int = field(
        default_factory=lambda: _env_int("PAGE_QA_MAX_REQUEST_BYTES", 2_000_000)
    )
    # Estimated-token thresholds: at or below the first the whole transcript is
    # sent; above it the context is a playback window plus BM25 hits capped at
    # the second.  Conservative defaults because the model window is unverified.
    page_qa_full_context_tokens: int = field(
        default_factory=lambda: _env_int("PAGE_QA_FULL_CONTEXT_TOKENS", 24_000)
    )
    page_qa_window_context_tokens: int = field(
        default_factory=lambda: _env_int("PAGE_QA_WINDOW_CONTEXT_TOKENS", 12_000)
    )
    # Questions per user per 10 minutes (in-process sliding window per worker).
    page_qa_rate_limit: int = field(
        default_factory=lambda: _env_int("PAGE_QA_RATE_LIMIT", 30)
    )
    web_session_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_SESSION_TTL_SECONDS", 30 * 24 * 60 * 60)
    )
    web_session_cache_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_SESSION_CACHE_TTL_SECONDS", 60)
    )
    web_auth_code_ttl_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_CODE_TTL_SECONDS", 600)
    )
    web_auth_max_attempts: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_MAX_ATTEMPTS", 5)
    )
    web_auth_resend_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_RESEND_SECONDS", 60)
    )
    web_auth_email_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_EMAIL_WINDOW_SECONDS", 900)
    )
    web_auth_email_max_sends: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_EMAIL_MAX_SENDS", 3)
    )
    web_auth_ip_window_seconds: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_IP_WINDOW_SECONDS", 3600)
    )
    web_auth_ip_max_sends: int = field(
        default_factory=lambda: _env_int("WEB_AUTH_IP_MAX_SENDS", 10)
    )
    # An unset provider deliberately keeps development/test mail in-process.
    # Production Web authentication must select an explicit network provider.
    email_provider: str | None = field(default_factory=lambda: _env("EMAIL_PROVIDER"))
    resend_api_key: str | None = field(default_factory=lambda: _env("RESEND_API_KEY"))
    resend_from_email: str | None = field(default_factory=lambda: _env("RESEND_FROM_EMAIL"))
    resend_timeout_seconds: float = field(
        default_factory=lambda: _env_float("RESEND_TIMEOUT_SECONDS", 10.0)
    )
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_username: str | None = field(default_factory=lambda: _env("SMTP_USERNAME"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_from_email: str | None = field(default_factory=lambda: _env("SMTP_FROM_EMAIL"))
    smtp_starttls: bool = field(
        default_factory=lambda: _env_bool("SMTP_STARTTLS", True)
    )
    smtp_timeout_seconds: float = field(
        default_factory=lambda: _env_float("SMTP_TIMEOUT_SECONDS", 10.0)
    )
    web_trusted_proxy_hosts: str = field(
        default_factory=lambda: _env("WEB_TRUSTED_PROXY_HOSTS", "") or ""
    )

    def _validate_wemm(self) -> None:
        if not isinstance(self.wemm_model, str) or not self.wemm_model.strip():
            raise ValueError("WEMM_MODEL must not be empty")
        if self.wemm_dimensions < 1:
            raise ValueError("WEMM_DIMENSIONS must be positive")
        if not isinstance(self.wemm_device, str) or not self.wemm_device.strip() or any(
            character.isspace() for character in self.wemm_device
        ):
            raise ValueError("WEMM_DEVICE must be a non-whitespace device")
        if not 1 <= self.wemm_batch_size <= 8:
            raise ValueError("WEMM_BATCH_SIZE must be between 1 and 8")
        if not 1 <= self.wemm_max_inputs <= 32:
            raise ValueError("WEMM_MAX_INPUTS must be between 1 and 32")
        if not 1 <= self.wemm_max_text_chars <= 8_192:
            raise ValueError("WEMM_MAX_TEXT_CHARS must be between 1 and 8192")
        if not 1 <= self.wemm_max_media_bytes <= 128 * 1024 * 1024:
            raise ValueError("WEMM_MAX_MEDIA_BYTES must be between 1 and 134217728")
        revision = self.wemm_revision
        if (
            not isinstance(revision, str)
            or not revision
            or revision != revision.strip()
            or any(character.isspace() for character in revision)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ValueError(
                "WEMM_ENABLED requires a 40-character commit SHA in WEMM_REVISION"
            )

    def _validate_asr(self) -> None:
        if not isinstance(self.dashscope_api_key, str) or not self.dashscope_api_key.strip():
            raise ValueError("DASHSCOPE_API_KEY is required when ASR_ENABLED=true")
        _validate_asr_https_url(
            "DASHSCOPE_BASE_URL", self.dashscope_base_url, allow_path=True
        )
        if not isinstance(self.fun_asr_model, str) or not self.fun_asr_model.strip():
            raise ValueError("FUN_ASR_MODEL must not be empty")
        if not self.asr_public_base_url:
            raise ValueError("ASR_PUBLIC_BASE_URL is required when ASR_ENABLED=true")
        _validate_asr_https_url(
            "ASR_PUBLIC_BASE_URL", self.asr_public_base_url, allow_path=False
        )
        if (
            not isinstance(self.asr_audio_url_secret, str)
            or len(self.asr_audio_url_secret) < 32
        ):
            raise ValueError(
                "ASR_AUDIO_URL_SECRET must contain at least 32 characters"
            )
        bounded_ints = (
            ("ASR_AUDIO_URL_TTL_SECONDS", self.asr_audio_url_ttl_seconds, 300, 86_400),
            ("ASR_MAX_DURATION_SECONDS", self.asr_max_duration_seconds, 60, 43_200),
            (
                "ASR_MAX_AUDIO_BYTES",
                self.asr_max_audio_bytes,
                1024 * 1024,
                2 * 1024 * 1024 * 1024,
            ),
            ("ASR_DOWNLOAD_TIMEOUT_SECONDS", self.asr_download_timeout_seconds, 30, 3_000),
            ("ASR_PROVIDER_TIMEOUT_SECONDS", self.asr_provider_timeout_seconds, 60, 3_000),
        )
        for name, value, low, high in bounded_ints:
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        if (
            not math.isfinite(self.asr_poll_interval_seconds)
            or not 1 <= self.asr_poll_interval_seconds <= 60
        ):
            raise ValueError("ASR_POLL_INTERVAL_SECONDS must be between 1 and 60")

    def __post_init__(self) -> None:
        if isinstance(self.wemm_enabled, _WemmEnv):
            object.__setattr__(
                self,
                "wemm_enabled",
                self.wemm_enabled.resolve(enabled=self.multimodal_retrieval_enabled),
            )
        wemm_active = self.multimodal_retrieval_enabled and self.wemm_enabled
        for name, value in vars(self).items():
            if isinstance(value, _WemmEnv):
                object.__setattr__(self, name, value.resolve(enabled=wemm_active))
        if wemm_active:
            self._validate_wemm()
        if self.notebook_agent_env not in {"development", "production"}:
            raise ValueError("STASHSEEK_ENV must be development or production")
        if (
            self.notebook_agent_log_retrieval_content
            and self.notebook_agent_env != "development"
        ):
            raise ValueError(
                "retrieval content logging requires STASHSEEK_ENV=development"
            )
        if not self.mcp_host.strip():
            raise ValueError("MCP_HOST must not be empty")
        if self.mcp_port < 1 or self.mcp_port > 65535:
            raise ValueError("MCP_PORT must be between 1 and 65535")
        if (
            not self.mcp_path.startswith("/")
            or self.mcp_path == "/"
            or "?" in self.mcp_path
            or "#" in self.mcp_path
        ):
            raise ValueError(
                "MCP_PATH must be an absolute path without query or fragment"
            )
        if self.mcp_path != "/" and self.mcp_path.endswith("/"):
            raise ValueError("MCP_PATH must not have a trailing slash")
        if self.web_api_prefix != "/api/v1":
            raise ValueError("WEB_API_PREFIX is fixed at /api/v1")
        positive_web_values = (
            self.web_session_ttl_seconds,
            self.web_auth_code_ttl_seconds,
            self.web_auth_max_attempts,
            self.web_auth_resend_seconds,
            self.web_auth_email_window_seconds,
            self.web_auth_email_max_sends,
            self.web_auth_ip_window_seconds,
            self.web_auth_ip_max_sends,
            self.browser_companion_pairing_ttl_seconds,
            self.browser_companion_grant_ttl_seconds,
            self.browser_companion_max_request_bytes,
        )
        if (
            any(value <= 0 for value in positive_web_values)
            or self.resend_timeout_seconds <= 0
            or self.smtp_timeout_seconds <= 0
            or self.web_session_cache_ttl_seconds < 0
        ):
            raise ValueError(
                "Web authentication durations and limits must be positive; "
                "the Session cache TTL may be zero"
            )
        if not 0 < self.page_qa_max_request_bytes <= self.browser_companion_max_request_bytes:
            raise ValueError(
                "PAGE_QA_MAX_REQUEST_BYTES must be positive and must not exceed "
                "BROWSER_COMPANION_MAX_REQUEST_BYTES"
            )
        if (
            self.page_qa_full_context_tokens <= 0
            or self.page_qa_window_context_tokens <= 0
            or self.page_qa_rate_limit <= 0
        ):
            raise ValueError(
                "PAGE_QA_FULL_CONTEXT_TOKENS, PAGE_QA_WINDOW_CONTEXT_TOKENS and "
                "PAGE_QA_RATE_LIMIT must be positive"
            )
        if self.page_qa_window_context_tokens > self.page_qa_full_context_tokens:
            raise ValueError(
                "PAGE_QA_WINDOW_CONTEXT_TOKENS must not exceed "
                "PAGE_QA_FULL_CONTEXT_TOKENS"
            )
        for extension_origin in self.browser_companion_allowed_origins:
            if extension_origin == "chrome-extension://*":
                if (
                    self.notebook_agent_env != "development"
                    or self.web_host not in {"127.0.0.1", "localhost", "::1"}
                ):
                    raise ValueError(
                        "wildcard browser companion origins require a loopback-only "
                        "development Web server"
                    )
                continue
            parsed_extension = urlsplit(extension_origin)
            extension_id = parsed_extension.netloc
            if (
                parsed_extension.scheme != "chrome-extension"
                or len(extension_id) != 32
                or any(character not in "abcdefghijklmnop" for character in extension_id)
                or parsed_extension.path
                or parsed_extension.query
                or parsed_extension.fragment
                or extension_origin.endswith("/")
            ):
                raise ValueError(
                    "BROWSER_COMPANION_ALLOWED_ORIGINS must contain exact "
                    "chrome-extension origins, or the development-only "
                    "chrome-extension://* wildcard"
                )
        if self.web_auth_enabled:
            parsed_origin = urlsplit(self.web_public_origin or "")
            if (
                parsed_origin.scheme != "https"
                or not parsed_origin.netloc
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("WEB_PUBLIC_ORIGIN must be an HTTPS origin when Web auth is enabled")
            if self.web_public_origin.rstrip("/") != self.web_public_origin:
                raise ValueError("WEB_PUBLIC_ORIGIN must not have a trailing slash")
            if not self.web_auth_secret or len(self.web_auth_secret) < 32:
                raise ValueError("WEB_AUTH_SECRET must be at least 32 characters")
            provider = (self.email_provider or "").strip().lower()
            if provider not in {"", "resend", "smtp"}:
                raise ValueError("EMAIL_PROVIDER must be resend or smtp")
            if self.notebook_agent_env == "production" and not provider:
                raise ValueError("production Web auth requires EMAIL_PROVIDER")
            if provider == "resend" and (
                not self.resend_api_key or not self.resend_from_email
            ):
                raise ValueError("EMAIL_PROVIDER=resend requires RESEND_API_KEY and RESEND_FROM_EMAIL")
            if provider == "smtp":
                if not all((
                    self.smtp_host,
                    self.smtp_username,
                    self.smtp_password,
                    self.smtp_from_email,
                )):
                    raise ValueError(
                        "EMAIL_PROVIDER=smtp requires SMTP_HOST, SMTP_USERNAME, "
                        "SMTP_PASSWORD, and SMTP_FROM_EMAIL"
                    )
                if not 1 <= self.smtp_port <= 65535:
                    raise ValueError("SMTP_PORT must be between 1 and 65535")
                smtp_header_values = (
                    self.smtp_host,
                    self.smtp_username,
                    self.smtp_from_email,
                )
                if any(
                    "\n" in value or "\r" in value for value in smtp_header_values
                ):
                    raise ValueError("SMTP settings must not contain line breaks")
        self.validate_external_login()
        if self.agent_composer_max_tokens <= 0:
            raise ValueError("AGENT_COMPOSER_MAX_TOKENS must be positive")
        if self.trash_retention_days <= 0:
            raise ValueError("TRASH_RETENTION_DAYS must be positive")
        if not _PLAUSIBLE_EMAIL.fullmatch(self.privacy_contact_email):
            raise ValueError("PRIVACY_CONTACT_EMAIL must be a valid email address")
        if self.trash_purge_interval_seconds <= 0:
            raise ValueError("TRASH_PURGE_INTERVAL_SECONDS must be positive")
        if self.trash_purge_batch_size <= 0 or self.trash_purge_batch_size > 100:
            raise ValueError("TRASH_PURGE_BATCH_SIZE must be between 1 and 100")
        if self.trash_purge_claim_timeout_seconds <= 0:
            raise ValueError("TRASH_PURGE_CLAIM_TIMEOUT_SECONDS must be positive")
        if self.trash_purge_max_duration_seconds <= 0:
            raise ValueError("TRASH_PURGE_MAX_DURATION_SECONDS must be positive")
        if self.trash_purge_object_timeout_seconds <= 0:
            raise ValueError("TRASH_PURGE_OBJECT_TIMEOUT_SECONDS must be positive")
        if self.ingest_completion_interval_seconds <= 0:
            raise ValueError("INGEST_COMPLETION_INTERVAL_SECONDS must be positive")
        if (
            self.ingest_completion_batch_size <= 0
            or self.ingest_completion_batch_size > 100
        ):
            raise ValueError("INGEST_COMPLETION_BATCH_SIZE must be between 1 and 100")
        if self.ingest_completion_claim_timeout_seconds <= 0:
            raise ValueError(
                "INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS must be positive"
            )
        if self.ingest_completion_max_duration_seconds <= 0:
            raise ValueError(
                "INGEST_COMPLETION_MAX_DURATION_SECONDS must be positive"
            )
        if min(
            self.ingest_max_raw_transcript_bytes,
            self.ingest_max_cues_per_item,
            self.ingest_max_text_chars_per_item,
            self.ingest_max_segments_per_item,
            self.ingest_max_embedding_chars_per_item,
        ) <= 0:
            raise ValueError("INGEST_CONTENT_LIMITS must be positive")
        if self.youtube_fetch_timeout_seconds <= 0:
            raise ValueError("YOUTUBE_FETCH_TIMEOUT_SECONDS must be positive")
        if self.bilibili_fetch_timeout_seconds <= 0:
            raise ValueError("BILIBILI_FETCH_TIMEOUT_SECONDS must be positive")
        if "YOUTUBE_PROXY_URL" in os.environ:
            # Renamed, not aliased: silently ignoring the old key would send
            # video fetches over the direct (blocked) route after a release.
            raise ValueError(
                "YOUTUBE_PROXY_URL was renamed to US_VIDEO_PROXY_URL; "
                "move the value and remove the old variable"
            )
        _validate_us_video_proxy_url(self.us_video_proxy_url)
        if self.asr_enabled:
            self._validate_asr()
        if self.ingest_notification_interval_seconds <= 0:
            raise ValueError("INGEST_NOTIFICATION_INTERVAL_SECONDS must be positive")
        if self.ingest_notification_full_sweep_interval_seconds <= 0:
            raise ValueError(
                "INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS must be positive"
            )
        if (
            self.ingest_notification_batch_size <= 0
            or self.ingest_notification_batch_size > 100
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_BATCH_SIZE must be between 1 and 100"
            )
        if self.ingest_notification_claim_timeout_seconds <= 0:
            raise ValueError(
                "INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS must be positive"
            )
        if (
            not math.isfinite(self.ingest_notification_max_duration_seconds)
            or self.ingest_notification_max_duration_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_MAX_DURATION_SECONDS must be positive"
            )
        if (
            self.ingest_notification_max_duration_seconds
            >= self.ingest_notification_interval_seconds
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_MAX_DURATION_SECONDS must be less than "
                "INGEST_NOTIFICATION_INTERVAL_SECONDS"
            )
        if self.ingest_notification_max_attempts <= 0:
            raise ValueError("INGEST_NOTIFICATION_MAX_ATTEMPTS must be positive")
        if (
            not math.isfinite(self.ingest_notification_retry_base_seconds)
            or self.ingest_notification_retry_base_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_BASE_SECONDS must be positive"
            )
        if (
            not math.isfinite(self.ingest_notification_retry_max_seconds)
            or self.ingest_notification_retry_max_seconds <= 0
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_MAX_SECONDS must be positive"
            )
        if (
            self.ingest_notification_retry_base_seconds
            > self.ingest_notification_retry_max_seconds
        ):
            raise ValueError(
                "INGEST_NOTIFICATION_RETRY_BASE_SECONDS must not exceed "
                "INGEST_NOTIFICATION_RETRY_MAX_SECONDS"
            )
        if (
            not math.isfinite(self.langbot_outbound_timeout_seconds)
            or self.langbot_outbound_timeout_seconds <= 0
        ):
            raise ValueError("LANGBOT_OUTBOUND_TIMEOUT_SECONDS must be positive")
        _validate_langbot_outbound_url(self.langbot_outbound_base_url)
        if (
            self.agent_composer_max_tokens * COMPOSER_VALIDATION_REQUEST_LIMIT
            > self.agent_output_token_limit
        ):
            raise ValueError(
                "AGENT_COMPOSER_MAX_TOKENS multiplied by the Composer request "
                "limit must not exceed AGENT_OUTPUT_TOKEN_LIMIT"
            )

    def validate_external_login(self) -> None:
        """Fail startup for enabled providers without their email/auth boundary."""

        enabled = (
            (self.web_microsoft_enabled, "WEB_MICROSOFT", (self.web_microsoft_client_id, self.web_microsoft_client_secret)),
            (self.web_wechat_website_enabled, "WEB_WECHAT_WEBSITE", (self.web_wechat_website_app_id, self.web_wechat_website_app_secret)),
            (self.web_qq_enabled, "WEB_QQ", (self.web_qq_app_id, self.web_qq_app_key)),
        )
        if not any(flag for flag, _name, _credentials in enabled):
            return
        if not self.web_auth_enabled:
            raise ValueError("external login requires WEB_AUTH_ENABLED")
        if not self.web_cookie_secure:
            raise ValueError("external login requires WEB_COOKIE_SECURE")
        for flag, name, credentials in enabled:
            if flag and not all(value and value.strip() for value in credentials):
                raise ValueError(f"{name} requires its application ID and secret")
        if self.web_microsoft_enabled:
            tenant = self.web_microsoft_tenant
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", tenant):
                raise ValueError("WEB_MICROSOFT_TENANT must be a tenant identifier")

    def validate_web_auth(self) -> None:
        if self.web_auth_secret is None or len(self.web_auth_secret) < 32:
            raise ValueError("WEB_AUTH_SECRET must contain at least 32 characters")
        if self.web_auth_challenge_ttl_seconds <= 0:
            raise ValueError("WEB_AUTH_CHALLENGE_TTL_SECONDS must be positive")
        if self.web_auth_session_ttl_seconds <= 0:
            raise ValueError("WEB_AUTH_SESSION_TTL_SECONDS must be positive")
        if self.web_auth_attempt_limit <= 0:
            raise ValueError("WEB_AUTH_ATTEMPT_LIMIT must be positive")
        if min(
            self.web_auth_rate_window_seconds,
            self.web_auth_rate_limit_per_requester,
            self.web_auth_global_rate_limit,
            self.web_auth_active_challenge_limit,
            self.web_auth_challenge_retention_seconds,
            self.web_auth_session_retention_seconds,
        ) <= 0:
            raise ValueError("Web auth rate and retention limits must be positive")
        if (
            self.web_auth_challenge_retention_seconds
            < self.web_auth_rate_window_seconds
        ):
            raise ValueError(
                "WEB_AUTH_CHALLENGE_RETENTION_SECONDS must cover the rate window"
            )
        if not self.web_host.strip():
            raise ValueError("WEB_HOST must not be blank")
        if not (1 <= self.web_port <= 65535):
            raise ValueError("WEB_PORT must be between 1 and 65535")
        if not self.web_static_dir.strip():
            raise ValueError("WEB_STATIC_DIR must not be blank")
        if self.web_publish_budget_seconds <= 0:
            raise ValueError("WEB_PUBLISH_BUDGET_SECONDS must be positive")
        if min(
            self.ingest_max_active_per_user,
            self.ingest_daily_new_item_limit,
            self.ingest_max_items_per_user,
            self.ingest_max_active_global,
            self.ingest_daily_new_item_limit_global,
            self.ingest_daily_dispatch_limit_per_user,
            self.ingest_daily_dispatch_limit_global,
        ) <= 0:
            raise ValueError("Ingest tenant limits must be positive")
        forwarded_sources = {
            value.strip()
            for value in self.web_forwarded_allow_ips.split(",")
            if value.strip()
        }
        if not forwarded_sources or "*" in forwarded_sources:
            raise ValueError(
                "WEB_FORWARDED_ALLOW_IPS must list explicit trusted proxies"
            )
        if self.web_origin is not None:
            origin = self.web_origin.strip()
            parsed = urlsplit(origin)
            loopback_http = (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
            if (
                (parsed.scheme != "https" and not loopback_http)
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("WEB_ORIGIN must be HTTPS or a loopback HTTP origin")
            if origin.endswith("/"):
                raise ValueError("WEB_ORIGIN must not include a trailing slash")

    @property
    def ingest_completion_publish_interval_seconds(self) -> int:
        """Compatibility spelling used by deployment operators."""

        return self.ingest_completion_interval_seconds

    @property
    def ingest_notification_retry_ceiling(self) -> int:
        """Compatibility alias for the configured notification attempt cap."""

        return self.ingest_notification_max_attempts


def _build_database_url() -> str:
    explicit = _env("DATABASE_URL")
    if explicit:
        return explicit
    user = _env("POSTGRES_USER", "postgres")
    password = _require("POSTGRES_PASSWORD")
    host = _env("POSTGRES_HOST", "localhost")
    port = _env("POSTGRES_PORT", "5432")
    db = _env("POSTGRES_DB", "kb")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def _build_redis_url() -> str:
    explicit = _env("REDIS_URL")
    if explicit:
        return explicit
    host = _env("REDIS_HOST", "localhost")
    port = _env("REDIS_PORT", "6379")
    db = _env("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
