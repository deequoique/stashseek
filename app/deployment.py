"""Profile-aware local and single-host deployment lifecycle.

This module deliberately manages application processes without replacing the
existing direct CLI/Celery entry points.  It is an operator convenience layer,
not a production secret manager or host provisioner.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
MANAGED_ENV = ROOT / ".env.runtime"
OPERATOR_ENV = ROOT / ".env"
STATE_DIR = ROOT / ".runtime" / "deployment"
STATE_FILE = STATE_DIR / "runtime.json"
LOCK_FILE = STATE_DIR / "lifecycle.lock"
LOG_DIR = STATE_DIR / "logs"
PROFILES = ("read", "full", "langbot")
FULL_DEPENDENCY_NAMES = (
    "database",
    "broker",
    "object_store",
    "maintenance",
    "worker",
)
FULL_RUNTIME_CHECK_TIMEOUT_SECONDS = 45
LISTENER_WAIT_TIMEOUT_SECONDS = 60
STARTUP_WAIT_TIMEOUT_SECONDS = 90
STOP_WAIT_TIMEOUT_SECONDS = 20


class DeploymentError(RuntimeError):
    """A bounded, operator-safe deployment failure."""


@dataclass(frozen=True)
class RuntimePlan:
    profile: str
    compose_services: tuple[str, ...]
    components: tuple[str, ...]


def _is_loopback_url(
    value: str,
    default_port: int,
    *,
    schemes: set[str] | None = None,
) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port or default_port
    except ValueError as exc:
        raise DeploymentError("invalid service URL configuration") from exc
    if not parsed.scheme or not host:
        raise DeploymentError("invalid service URL configuration")
    return (
        (schemes is None or parsed.scheme in schemes)
        and host in {"localhost", "127.0.0.1", "::1"}
        and port == default_port
    )


def _env_port(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        port = int(env.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise DeploymentError(f"{name} must be an integer") from exc
    if port < 1 or port > 65535:
        raise DeploymentError(f"{name} must be between 1 and 65535")
    return port


def _compat_value(
    env: Mapping[str, str], canonical: str, legacy: str, default: str | None = None
) -> str | None:
    """Resolve a renamed launcher variable with canonical precedence."""

    if canonical in env:
        return env[canonical]
    return env.get(legacy, default)


_COMPAT_ENV_PAIRS: tuple[tuple[str, str], ...] = (
    ("STASHSEEK_PROFILE", "NOTEBOOK_AGENT_PROFILE"),
    ("STASHSEEK_ALLOW_NON_LOOPBACK", "NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK"),
    ("STASHSEEK_LOG_MAX_BYTES", "NOTEBOOK_AGENT_LOG_MAX_BYTES"),
    ("STASHSEEK_LOG_BACKUP_COUNT", "NOTEBOOK_AGENT_LOG_BACKUP_COUNT"),
)


def _canonicalize_compat_layer(values: Mapping[str, str]) -> dict[str, str]:
    """Project legacy values into canonical keys within one source layer."""

    normalized = dict(values)
    for canonical, legacy in _COMPAT_ENV_PAIRS:
        if canonical not in normalized and legacy in normalized:
            normalized[canonical] = normalized[legacy]
    return normalized


def _listener_targets(
    profile: str, env: Mapping[str, str]
) -> tuple[tuple[str, str, int], ...]:
    if profile not in PROFILES:
        raise DeploymentError(f"unknown profile: {profile}")
    targets: list[tuple[str, str, int]] = []
    if profile in {"read", "full"}:
        targets.append(
            (
                "mcp",
                env.get("MCP_HOST", "127.0.0.1"),
                _env_port(env, "MCP_PORT", 8000),
            )
        )
    if profile in {"full", "langbot"}:
        targets.append(
            (
                "gateway",
                env.get("CHANNEL_GATEWAY_HOST", "127.0.0.1"),
                _env_port(env, "CHANNEL_GATEWAY_PORT", 8765),
            )
        )
    return tuple(targets)


def build_plan(profile: str, env: Mapping[str, str]) -> RuntimePlan:
    if profile not in PROFILES:
        raise DeploymentError(f"unknown profile: {profile}")
    services: list[str] = []
    postgres_host = env.get("POSTGRES_HOST", "localhost").lower()
    if not env.get("DATABASE_URL") and postgres_host in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        services.append("postgres")
    if profile in {"full", "langbot"}:
        redis_url = env.get("REDIS_URL")
        redis_host = env.get("REDIS_HOST", "localhost").lower()
        redis_port = _env_port(env, "REDIS_PORT", 6379)
        if (
            redis_url
            and _is_loopback_url(
                redis_url, redis_port, schemes={"redis"}
            )
        ) or (
            not redis_url and redis_host in {"localhost", "127.0.0.1", "::1"}
        ):
            services.append("redis")
        minio_url = env.get("MINIO_ENDPOINT_URL", "http://localhost:9000")
        minio_port = _env_port(env, "MINIO_API_PORT", 9000)
        if _is_loopback_url(minio_url, minio_port, schemes={"http"}):
            services.append("minio")

    if profile == "read":
        components = ("mcp",)
    elif profile == "full":
        components = ("worker", "beat", "mcp", "gateway")
    else:
        components = ("worker", "beat", "gateway")
    return RuntimePlan(profile, tuple(services), components)


def load_environment(
    process_env: Mapping[str, str] | None = None,
    *,
    managed_path: Path = MANAGED_ENV,
    operator_path: Path = OPERATOR_ENV,
) -> dict[str, str]:
    """Resolve process > operator .env > generated environment."""

    resolved: dict[str, str] = {}
    if managed_path.exists():
        resolved.update(
            _canonicalize_compat_layer({
                key: value
                for key, value in dotenv_values(
                    managed_path, interpolate=False
                ).items()
                if value is not None
            })
        )
    if operator_path.exists():
        resolved.update(
            _canonicalize_compat_layer({
                key: value
                for key, value in dotenv_values(operator_path).items()
                if value is not None
            })
        )
    resolved.update(
        _canonicalize_compat_layer(
            process_env if process_env is not None else os.environ
        )
    )
    return resolved


def required_variables(profile: str, env: Mapping[str, str]) -> tuple[str, ...]:
    required = ["ZHIPU_API_KEY"]
    if not env.get("DATABASE_URL"):
        required.append("POSTGRES_PASSWORD")
    if profile in {"full", "langbot"}:
        required.extend(("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"))
        required.append("CHANNEL_GATEWAY_SECRET")
    return tuple(name for name in required if not env.get(name))


def _secret_value(prompt: str) -> str:
    import getpass

    return getpass.getpass(prompt).strip()


def _write_private_env(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for key in sorted(values):
                value = values[key]
                if "\n" in value or "\r" in value:
                    raise DeploymentError(f"{key} must be a single-line value")
                escaped = value.replace("\\", "\\\\").replace("'", "\\'")
                stream.write(f"{key}='{escaped}'\n")
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def initialize(profile: str, *, force: bool = False) -> None:
    if MANAGED_ENV.exists() and not force:
        raise DeploymentError(
            f"managed configuration already exists: {MANAGED_ENV.name}; use --force to replace it"
        )
    inherited = load_environment(
        managed_path=MANAGED_ENV, operator_path=OPERATOR_ENV
    )
    # Write the canonical key and a same-value compatibility alias. Existing
    # operators can continue to inspect or invoke the legacy launcher while
    # new processes deterministically read STASHSEEK_PROFILE first.
    values: dict[str, str] = {
        "STASHSEEK_PROFILE": profile,
        "NOTEBOOK_AGENT_PROFILE": profile,
    }
    if MANAGED_ENV.exists():
        old_managed = {
            key: value
            for key, value in dotenv_values(
                MANAGED_ENV, interpolate=False
            ).items()
            if value is not None
        }
        preserved = {"ZHIPU_API_KEY", "AGENT_API_KEY", "POSTGRES_PASSWORD"}
        if profile in {"full", "langbot"}:
            preserved.update({"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"})
            preserved.add("CHANNEL_GATEWAY_SECRET")
        values.update(
            {key: old_managed[key] for key in preserved if key in old_managed}
        )
        values["STASHSEEK_PROFILE"] = profile
        values["NOTEBOOK_AGENT_PROFILE"] = profile

    if not inherited.get("DATABASE_URL") and not inherited.get("POSTGRES_PASSWORD"):
        postgres_host = inherited.get("POSTGRES_HOST", "localhost").lower()
        if postgres_host in {"localhost", "127.0.0.1", "::1"}:
            values["POSTGRES_PASSWORD"] = secrets.token_urlsafe(24)
    if profile in {"full", "langbot"}:
        minio_url = inherited.get("MINIO_ENDPOINT_URL", "http://localhost:9000")
        minio_port = _env_port(inherited, "MINIO_API_PORT", 9000)
        if _is_loopback_url(minio_url, minio_port, schemes={"http"}):
            if not inherited.get("MINIO_ROOT_USER"):
                values["MINIO_ROOT_USER"] = "notebook-agent"
            if not inherited.get("MINIO_ROOT_PASSWORD"):
                values["MINIO_ROOT_PASSWORD"] = secrets.token_urlsafe(32)
    if profile in {"full", "langbot"} and not inherited.get(
        "CHANNEL_GATEWAY_SECRET"
    ):
        values["CHANNEL_GATEWAY_SECRET"] = secrets.token_urlsafe(32)

    prompts = [("ZHIPU_API_KEY", "Embedding provider API key: ")]
    if not inherited.get("DATABASE_URL") and not inherited.get("POSTGRES_PASSWORD"):
        if "POSTGRES_PASSWORD" not in values:
            prompts.append(("POSTGRES_PASSWORD", "PostgreSQL password: "))
    for name, label in prompts:
        if inherited.get(name):
            continue
        if not sys.stdin.isatty():
            raise DeploymentError(
                f"missing required environment variable: {name} (set it before non-interactive init)"
            )
        value = _secret_value(label)
        if not value:
            raise DeploymentError(f"missing required environment variable: {name}")
        values[name] = value

    if not inherited.get("AGENT_API_KEY") and sys.stdin.isatty():
        value = _secret_value(
            "Agent provider API key (blank uses the provider's native environment): "
        )
        if value:
            values["AGENT_API_KEY"] = value

    _write_private_env(MANAGED_ENV, values)
    print(f"Created {MANAGED_ENV.name} with profile={profile} (secret values hidden).")


def _component_commands(profile: str) -> dict[str, list[str]]:
    python = sys.executable
    commands = {
        "mcp": [python, "-m", "app.cli", "mcp-server", "--transport", "streamable-http"],
        "gateway": [python, "-m", "app.cli", "gateway-server"],
        "worker": [
            python,
            "-m",
            "celery",
            "-A",
            "app.ingest.tasks.celery_app",
            "worker",
            "--loglevel=INFO",
            "--queues=ingest,maintenance",
            "--concurrency=1",
        ],
        "beat": [
            python,
            "-m",
            "celery",
            "-A",
            "app.ingest.tasks.celery_app",
            "beat",
            "--loglevel=INFO",
            "--schedule=.runtime/deployment/celerybeat-schedule",
        ],
    }
    plan = build_plan(profile, {})
    return {name: commands[name] for name in plan.components}


def _run_checked(
    command: Sequence[str],
    env: Mapping[str, str],
    *,
    timeout: float = 90,
) -> None:
    try:
        subprocess.run(
            command,
            cwd=ROOT,
            env=dict(env),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DeploymentError(f"required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise DeploymentError(f"command failed during deployment: {Path(command[0]).name}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeploymentError(
            f"command timed out during deployment: {Path(command[0]).name}"
        ) from exc


def _database_target(
    value: str,
) -> tuple[str, str, dict[str, list[str]]]:
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise DeploymentError("invalid database URL configuration") from exc
    if not parsed.hostname:
        raise DeploymentError("invalid database URL configuration")
    database_name = parsed.path.lstrip("/")
    if not database_name:
        raise DeploymentError("database URL must include a database name")
    return parsed.hostname.lower(), database_name, parse_qs(parsed.query)


def _url_health_target(value: str) -> dict[str, object]:
    try:
        parsed = urlparse(value)
        return {
            "scheme": parsed.scheme.lower(),
            "host": (parsed.hostname or "").lower(),
            "port": parsed.port,
            "username": parsed.username or "",
            "path": parsed.path,
            "sslmode": parse_qs(parsed.query).get("sslmode", []),
        }
    except ValueError:
        return {"invalid": True}


def _health_target_fingerprint(
    profile: str, env: Mapping[str, str]
) -> str:
    """Hash non-secret service targets used by an on-demand status probe."""

    database_url = env.get("DATABASE_URL", "").strip()
    targets: dict[str, object] = {
        "profile": profile,
        "database": (
            _url_health_target(database_url)
            if database_url
            else {
                "host": env.get("POSTGRES_HOST", "localhost").lower(),
                "port": env.get("POSTGRES_PORT", "5432"),
                "username": env.get("POSTGRES_USER", "postgres"),
                "database": env.get("POSTGRES_DB", "kb"),
            }
        ),
        "compose_project": env.get("COMPOSE_PROJECT_NAME", ROOT.name),
    }
    if profile in {"full", "langbot"}:
        redis_url = env.get("REDIS_URL", "").strip()
        targets["redis"] = (
            _url_health_target(redis_url)
            if redis_url
            else {
                "host": env.get("REDIS_HOST", "localhost").lower(),
                "port": env.get("REDIS_PORT", "6379"),
                "database": env.get("REDIS_DB", "0"),
            }
        )
        targets["object_store"] = {
            "endpoint": _url_health_target(
                env.get("MINIO_ENDPOINT_URL", "http://localhost:9000")
            ),
            "bucket": env.get("MINIO_BUCKET", "kb-raw"),
        }
    targets["listeners"] = [
        {"name": name, "host": host.lower(), "port": port}
        for name, host, port in _listener_targets(profile, env)
    ]
    encoded = json.dumps(targets, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _migration_environment(env: Mapping[str, str]) -> dict[str, str]:
    migration_env = dict(env)
    migration_url = env.get("MIGRATION_DATABASE_URL")
    runtime_url = env.get("DATABASE_URL")
    runtime_host = ""
    runtime_database = ""
    runtime_query: dict[str, list[str]] = {}
    if runtime_url:
        runtime_host, runtime_database, runtime_query = _database_target(runtime_url)
        if "-pooler." in runtime_host and runtime_query.get("sslmode") != ["require"]:
            raise DeploymentError("pooled Neon DATABASE_URL must require TLS")
    if migration_url:
        migration_host, migration_database, query = _database_target(migration_url)
        if not runtime_url:
            raise DeploymentError(
                "MIGRATION_DATABASE_URL requires an explicit DATABASE_URL"
            )
        if "-pooler." in migration_host:
            raise DeploymentError("MIGRATION_DATABASE_URL must use a direct database host")
        if migration_database != runtime_database:
            raise DeploymentError(
                "MIGRATION_DATABASE_URL must target the runtime database name"
            )
        if "-pooler." in runtime_host:
            expected_host = runtime_host.replace("-pooler.", ".", 1)
            if migration_host != expected_host:
                raise DeploymentError(
                    "MIGRATION_DATABASE_URL must target the runtime database's direct host"
                )
            if query.get("sslmode") != ["require"]:
                raise DeploymentError(
                    "MIGRATION_DATABASE_URL must require TLS for Neon"
                )
        elif migration_host != runtime_host:
            raise DeploymentError(
                "MIGRATION_DATABASE_URL must target the runtime database host"
            )
        migration_env["DATABASE_URL"] = migration_url
    elif "-pooler." in runtime_host:
        raise DeploymentError(
            "MIGRATION_DATABASE_URL is required when DATABASE_URL uses a pooled Neon host"
        )
    migration_env.pop("MIGRATION_DATABASE_URL", None)
    return migration_env


def _running_compose_services(env: Mapping[str, str]) -> set[str]:
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--status", "running", "--services"],
            cwd=ROOT,
            env=dict(env),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(
            "could not establish existing Compose service ownership"
        ) from exc
    return set(result.stdout.splitlines())


def _prepare(profile: str, env: Mapping[str, str]) -> None:
    missing = required_variables(profile, env)
    if missing:
        raise DeploymentError("missing required environment variables: " + ", ".join(missing))
    plan = build_plan(profile, env)
    listener_targets = _listener_targets(profile, env)
    if len({port for _name, _host, port in listener_targets}) != len(
        listener_targets
    ):
        raise DeploymentError("profile listeners must use distinct ports")
    if "gateway" in plan.components:
        if env.get("CHANNEL_GATEWAY_HOST", "127.0.0.1") not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise DeploymentError("CHANNEL_GATEWAY_HOST must use loopback")
        if len(env.get("CHANNEL_GATEWAY_SECRET", "")) < 32:
            raise DeploymentError(
                "CHANNEL_GATEWAY_SECRET must be at least 32 characters"
            )
    if "mcp" in plan.components:
        mcp_host = env.get("MCP_HOST", "127.0.0.1").lower()
        acknowledged = (
            _compat_value(
                env,
                "STASHSEEK_ALLOW_NON_LOOPBACK",
                "NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK",
                "",
            )
            or ""
        ).lower()
        if mcp_host not in {"localhost", "127.0.0.1", "::1"} and acknowledged not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise DeploymentError(
                "non-loopback MCP_HOST requires STASHSEEK_ALLOW_NON_LOOPBACK=true and a TLS proxy"
            )
    migration_env = _migration_environment(env)
    static_source = (
        "from pydantic_ai.models import infer_model; "
        "from app.agent.provider import build_model; "
        "from app.config import Settings; "
        "from app.tls import configure_trusted_ca; "
        "settings=Settings(); configure_trusted_ca(settings.tls_ca_bundle); "
        "infer_model(build_model(settings))"
    )
    _run_checked([sys.executable, "-c", static_source], env, timeout=15)
    # Complete every static preflight before starting local infrastructure.
    # In particular, a missing Neon direct migration URL must be side-effect
    # free instead of leaving Redis/MinIO running after the command fails.
    previously_running = _running_compose_services(env) if plan.compose_services else set()
    try:
        if plan.compose_services:
            _run_checked(
                [
                    "docker",
                    "compose",
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "60",
                    *plan.compose_services,
                ],
                env,
            )
        _run_checked(
            [sys.executable, "-m", "alembic", "upgrade", "head"], migration_env
        )
        if profile in {"full", "langbot"} and "minio" in plan.compose_services:
            source = (
                "from botocore.exceptions import ClientError; "
                "from app.ingest.tasks import RawObjectStore; "
                "store=RawObjectStore(); "
                "\ntry: store.client.head_bucket(Bucket=store.bucket)"
                "\nexcept ClientError: store.client.create_bucket(Bucket=store.bucket)"
            )
            _run_checked([sys.executable, "-c", source], env)
    except DeploymentError:
        newly_started = [
            service for service in plan.compose_services if service not in previously_running
        ]
        if newly_started:
            try:
                _run_checked(["docker", "compose", "stop", *newly_started], env)
            except DeploymentError:
                pass
        raise


def _read_state() -> dict[str, object] | None:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _remove_state(run_id: str) -> None:
    with _LifecycleLock():
        state = _read_state()
        if state and state.get("run_id") == run_id:
            STATE_FILE.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _pid_matches(pid: int, run_id: str) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return "app.deployment" in result.stdout and run_id in result.stdout


def _launcher_matches(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return "app.deployment" in result.stdout and any(
        command in result.stdout for command in (" start", " restart")
    )


class _LifecycleLock:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._stream = LOCK_FILE.open("a+", encoding="utf-8")
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args):
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()


def _active_state() -> dict[str, object] | None:
    state = _read_state()
    if not state:
        return None
    run_id = str(state.get("run_id", ""))
    try:
        if state.get("supervisor_pid") is not None:
            return (
                state
                if _pid_matches(int(state["supervisor_pid"]), run_id)
                else None
            )
        if state.get("launcher_pid") is not None:
            return state if _launcher_matches(int(state["launcher_pid"])) else None
    except (TypeError, ValueError):
        return None
    return None


def _write_reservation(run_id: str, profile: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "profile": profile,
        "phase": "starting",
        "launcher_pid": os.getpid(),
        "started_at": int(time.time()),
        "children": {},
    }
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _claim_reservation(run_id: str, profile: str) -> None:
    with _LifecycleLock():
        state = _read_state()
        if (
            not state
            or state.get("run_id") != run_id
            or state.get("profile") != profile
            or state.get("phase") != "starting"
        ):
            raise DeploymentError("invalid or already claimed supervisor reservation")
        try:
            launcher_pid = int(state["launcher_pid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeploymentError("invalid supervisor reservation") from exc
        if not _launcher_matches(launcher_pid):
            raise DeploymentError("supervisor launcher is no longer active")
        state["phase"] = "supervising"
        state["supervisor_pid"] = os.getpid()
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, STATE_FILE)


def start(profile: str | None, *, foreground: bool) -> None:
    if not MANAGED_ENV.exists():
        initialize(_auto_init_profile(profile))
    env = load_environment()
    selected = profile or (
        _compat_value(env, "STASHSEEK_PROFILE", "NOTEBOOK_AGENT_PROFILE", "full")
        or "full"
    )
    previous_handlers = {}
    process: subprocess.Popen | None = None
    pending_signals: list[int] = []

    def forward_signal(signum, _frame):
        if process is None:
            pending_signals.append(signum)
        elif process.poll() is None:
            process.send_signal(signum)

    try:
        with _LifecycleLock():
            active = _active_state()
            if active:
                phase = active.get("phase", "running")
                print(
                    f"StashSeek Chat is already {phase} (profile={active.get('profile')})."
                )
                return
            if STATE_FILE.exists():
                STATE_FILE.unlink()
            _prepare(selected, env)
            run_id = secrets.token_hex(16)
            _write_reservation(run_id, selected)
            runtime_env = dict(env)
            # Keep python-dotenv in application children from reloading a direct
            # migration DSN from the operator .env after we remove its value.
            runtime_env["MIGRATION_DATABASE_URL"] = ""
            command = [
                sys.executable,
                "-m",
                "app.deployment",
                "supervise",
                "--profile",
                selected,
                "--run-id",
                run_id,
            ]
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                if foreground:
                    # Install forwarding before spawning so a signal cannot land
                    # between Popen and handler registration and orphan the run.
                    for signum in (signal.SIGTERM, signal.SIGINT):
                        previous_handlers[signum] = signal.signal(
                            signum, forward_signal
                        )
                    process = subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=runtime_env,
                        start_new_session=True,
                    )
                    for signum in pending_signals:
                        process.send_signal(signum)
                    pending_signals.clear()
                else:
                    log = (LOG_DIR / "supervisor.log").open("a", encoding="utf-8")
                    try:
                        process = subprocess.Popen(
                            command,
                            cwd=ROOT,
                            env=runtime_env,
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                        )
                    finally:
                        log.close()
            except OSError as exc:
                STATE_FILE.unlink(missing_ok=True)
                raise DeploymentError("failed to launch the runtime supervisor") from exc
        assert process is not None
        # The launcher must outlive the bounded listener wait without killing
        # a supervisor whose application endpoint is still starting.
        deadline = time.monotonic() + STARTUP_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = _active_state()
            if (
                state
                and state.get("run_id") == run_id
                and state.get("phase") == "running"
            ):
                print(f"StashSeek Chat started (profile={selected}).")
                break
            if process.poll() is not None:
                _remove_state(run_id)
                raise DeploymentError("supervisor failed before runtime readiness")
            time.sleep(0.1)
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            _remove_state(run_id)
            raise DeploymentError(
                "supervisor did not become ready; inspect supervisor logs"
            )
        if not foreground:
            return
        raise SystemExit(process.wait())
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _auto_init_profile(explicit: str | None) -> str:
    if explicit:
        return explicit
    initial_env = load_environment(
        managed_path=Path("/__missing__"), operator_path=OPERATOR_ENV
    )
    selected = (
        _compat_value(
            initial_env, "STASHSEEK_PROFILE", "NOTEBOOK_AGENT_PROFILE", "full"
        )
        or "full"
    )
    if selected not in PROFILES:
        raise DeploymentError(f"unknown profile: {selected}")
    return selected


def _write_state(
    run_id: str,
    profile: str,
    children: Mapping[str, subprocess.Popen],
    checks: Mapping[str, bool],
    managed_services: Sequence[str],
    env: Mapping[str, str],
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "profile": profile,
        "phase": "running",
        "supervisor_pid": os.getpid(),
        "started_at": int(time.time()),
        "children": {name: child.pid for name, child in children.items()},
        "managed_services": list(managed_services),
        "health_target_fingerprint": _health_target_fingerprint(profile, env),
        "health_updated_at": int(time.time()),
        "checks": dict(checks),
    }
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _spawn_component(
    name: str,
    command: Sequence[str],
    env: Mapping[str, str],
    logs: list,
) -> subprocess.Popen:
    _log_rotation_config(env)
    child = subprocess.Popen(
        command,
        cwd=ROOT,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    if child.stdout is None:
        _stop_untracked_child(child)
        raise DeploymentError("component log capture is unavailable")
    try:
        logs.append(_ComponentLog(name, child.stdout, env))
    except Exception:
        _stop_untracked_child(child)
        raise
    return child


def _log_rotation_config(env: Mapping[str, str]) -> tuple[int, int]:
    try:
        max_bytes = int(
            _compat_value(
                env, "STASHSEEK_LOG_MAX_BYTES", "NOTEBOOK_AGENT_LOG_MAX_BYTES", "10485760"
            )
            or "10485760"
        )
        backup_count = int(
            _compat_value(
                env, "STASHSEEK_LOG_BACKUP_COUNT", "NOTEBOOK_AGENT_LOG_BACKUP_COUNT", "5"
            )
            or "5"
        )
    except (TypeError, ValueError) as exc:
        raise DeploymentError("invalid log rotation configuration") from exc
    if max_bytes < 1024 or backup_count < 1:
        raise DeploymentError("invalid log rotation configuration")
    return max_bytes, backup_count


def _stop_untracked_child(child: subprocess.Popen) -> None:
    _signal_child_group(child, signal.SIGTERM)
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_child_group(child, signal.SIGKILL)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError("failed to reap component after startup error") from exc


class _ComponentLog:
    def __init__(self, name: str, stream, env: Mapping[str, str]) -> None:
        max_bytes, backup_count = _log_rotation_config(env)
        self._stream = stream
        self._redactions = _secret_values(env)
        self._logger = logging.getLogger(
            f"notebook_agent.deployment.component.{name}.{id(self)}"
        )
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._handler = RotatingFileHandler(
            LOG_DIR / f"{name}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)
        self._thread = threading.Thread(
            target=self._pump,
            name=f"deployment-log-{name}",
            daemon=True,
        )
        self._thread.start()

    def _pump(self) -> None:
        for line in self._stream:
            safe_line = line.rstrip("\r\n")
            for value in self._redactions:
                safe_line = safe_line.replace(value, "[REDACTED]")
            self._logger.info(safe_line)

    def close(self) -> None:
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            self._stream.close()
            self._thread.join(timeout=1)
        self._logger.removeHandler(self._handler)
        self._handler.close()


def _secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    explicit = {
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "REDIS_URL",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "CHANNEL_GATEWAY_SECRET",
        "LANGBOT_OUTBOUND_API_KEY",
        "MCP_TOKEN",
    }
    values: set[str] = set()
    for key, value in env.items():
        if not value:
            continue
        upper = key.upper()
        secret = (
            upper in explicit
            or upper.endswith("_API_KEY")
            or upper.endswith("_PASSWORD")
            or upper.endswith("_SECRET")
            or upper.endswith("_TOKEN")
        )
        if upper.endswith("_URL"):
            try:
                parsed = urlparse(value)
                secret = secret or parsed.username is not None or parsed.password is not None
            except ValueError:
                secret = True
        if secret:
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def _children_alive(children: Mapping[str, subprocess.Popen]) -> bool:
    return all(child.poll() is None for child in children.values())


def _full_runtime_checks(env: Mapping[str, str]) -> dict[str, bool]:
    source = (
        "import json; from app.config import Settings; "
        "from app.db import get_session_factory; "
        "from app.mcp_readiness import assess_mcp_mutation_readiness; "
        "result=assess_mcp_mutation_readiness("
        "Settings(), session_factory=get_session_factory()); "
        "print(json.dumps(dict(result.checks), sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=FULL_RUNTIME_CHECK_TIMEOUT_SECONDS,
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {name: False for name in FULL_DEPENDENCY_NAMES}
    if not isinstance(value, dict):
        return {name: False for name in FULL_DEPENDENCY_NAMES}
    return {name: bool(value.get(name, False)) for name in FULL_DEPENDENCY_NAMES}


def _database_ready(env: Mapping[str, str]) -> bool:
    source = (
        "from sqlalchemy import text; from app.db import get_session_factory; "
        "factory=get_session_factory(); "
        "\nwith factory() as db: db.execute(text('SELECT 1'))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=dict(env),
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _compose_health(
    services: Sequence[str], env: Mapping[str, str]
) -> dict[str, bool]:
    if not services:
        return {}
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json", *services],
            cwd=ROOT,
            env=dict(env),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        raw = result.stdout.strip()
        if not raw:
            rows = []
        elif raw.startswith("["):
            rows = json.loads(raw)
        else:
            rows = [json.loads(line) for line in raw.splitlines()]
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return {service: False for service in services}
    by_service = {
        str(row.get("Service")): row
        for row in rows
        if isinstance(row, dict) and row.get("Service")
    }
    return {
        service: (
            str(by_service.get(service, {}).get("State", "")).lower()
            == "running"
            and str(by_service.get(service, {}).get("Health", "")).lower()
            == "healthy"
        )
        for service in services
    }


def _port_ready(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((target, port), timeout=0.25):
            return True
    except OSError:
        return False


def _wait_for_listener(
    profile: str,
    env: Mapping[str, str],
    children: Mapping[str, subprocess.Popen],
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    targets = _listener_targets(profile, env)
    deadline = time.monotonic() + LISTENER_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if should_stop():
            raise DeploymentError("runtime startup was interrupted")
        if not _children_alive(children):
            raise DeploymentError("a required application process exited during startup")
        if all(_port_ready(host, port) for _name, host, port in targets):
            return
        time.sleep(0.1)
    raise DeploymentError("application listener did not become ready")


def _signal_child_group(child: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def supervise(profile: str, run_id: str) -> int:
    _claim_reservation(run_id, profile)
    env = dict(os.environ)
    env["MIGRATION_DATABASE_URL"] = ""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    children: dict[str, subprocess.Popen] = {}
    logs = []
    stopping = False
    unexpected_exit = False

    def request_stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        commands = _component_commands(profile)
        # Preparation has already validated configuration, started owned
        # infrastructure, and applied migrations. Launch the complete profile
        # now; deep dependency probes are diagnostics, not lifecycle gates.
        for name, command in commands.items():
            children[name] = _spawn_component(name, command, env, logs)
        _wait_for_listener(profile, env, children, should_stop=lambda: stopping)
        managed_services = build_plan(profile, env).compose_services
        checks = {
            f"process.{name}": child.poll() is None
            for name, child in children.items()
        }
        checks.update({f"compose.{name}": True for name in managed_services})
        listener_targets = _listener_targets(profile, env)
        for name, _host, _port in listener_targets:
            key = "listener" if len(listener_targets) == 1 else f"listener.{name}"
            checks[key] = True
        _write_state(
            run_id, profile, children, checks, managed_services, env
        )
        while not stopping:
            for child in children.values():
                if child.poll() is not None:
                    unexpected_exit = True
                    stopping = True
                    break
            if not stopping:
                time.sleep(0.25)
    finally:
        for child in children.values():
            if child.poll() is None:
                _signal_child_group(child, signal.SIGTERM)
        deadline = time.monotonic() + 10
        for child in children.values():
            remaining = deadline - time.monotonic()
            if child.poll() is None and remaining > 0:
                try:
                    child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    pass
            if child.poll() is None:
                _signal_child_group(child, signal.SIGKILL)
        state = _read_state()
        if state and state.get("run_id") == run_id:
            STATE_FILE.unlink(missing_ok=True)
        for log in logs:
            log.close()
    failed = [child.returncode for child in children.values() if child.returncode not in (0, -15)]
    if unexpected_exit and not failed:
        return 1
    return failed[0] if failed else 0


def stop() -> None:
    claim_deadline = time.monotonic() + 5
    while True:
        with _LifecycleLock():
            state = _active_state()
            if not state:
                print("StashSeek Chat is not running.")
                return
            supervisor_pid = state.get("supervisor_pid")
            if supervisor_pid is not None:
                pid = int(supervisor_pid)
                os.kill(pid, signal.SIGTERM)
                break
        if time.monotonic() >= claim_deadline:
            raise DeploymentError(
                "runtime startup has not reached a stoppable supervisor state"
            )
        time.sleep(0.1)
    # The supervisor has no deep dependency probe in its lifecycle loop, so
    # this budget only needs to cover signal observation and child cleanup.
    deadline = time.monotonic() + STOP_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    if _pid_alive(pid):
        raise DeploymentError("supervisor did not stop within the safety timeout")
    print("StashSeek Chat stopped.")


def _child_pid_matches(name: str, pid: int) -> bool:
    signatures = {
        "mcp": ("app.cli", "mcp-server"),
        "gateway": ("app.cli", "gateway-server"),
        "worker": ("celery", "worker"),
        "beat": ("celery", "beat"),
    }
    expected = signatures.get(name)
    if expected is None or not _pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return all(token in result.stdout for token in expected)


def status() -> int:
    state = _active_state()
    if not state:
        print("StashSeek Chat: stopped")
        return 1
    profile = str(state.get("profile", ""))
    print(f"StashSeek Chat: running (profile={profile})")
    checks: dict[str, bool] = {}
    children = state.get("children", {})
    if isinstance(children, dict):
        for name, pid in sorted(children.items()):
            try:
                healthy = _child_pid_matches(str(name), int(pid))
            except (TypeError, ValueError):
                healthy = False
            checks[f"process.{name}"] = healthy
    env = load_environment()
    expected_target = state.get("health_target_fingerprint")
    if expected_target != _health_target_fingerprint(profile, env):
        checks["configuration.runtime"] = False
    else:
        if profile in {"full", "langbot"}:
            checks.update(
                {
                    f"dependency.{name}": ready
                    for name, ready in _full_runtime_checks(env).items()
                }
            )
        else:
            checks["dependency.database"] = _database_ready(env)
        raw_services = state.get("managed_services", [])
        managed_services = (
            tuple(str(service) for service in raw_services)
            if isinstance(raw_services, list)
            else ()
        )
        checks.update(
            {
                f"compose.{name}": ready
                for name, ready in _compose_health(managed_services, env).items()
            }
        )
        listener_targets = _listener_targets(profile, env)
        for name, host, port in listener_targets:
            key = "listener" if len(listener_targets) == 1 else f"listener.{name}"
            checks[key] = _port_ready(host, port)
    for name, healthy in sorted(checks.items()):
        print(f"  {name}: {'ready' if healthy else 'unavailable'}")
    return 0 if checks and all(checks.values()) else 1


def logs(component: str | None, *, follow: bool, lines: int) -> int:
    name = component or "supervisor"
    allowed = {"supervisor", "mcp", "gateway", "worker", "beat"}
    if name not in allowed:
        raise DeploymentError(f"unknown log component: {name}")
    path = LOG_DIR / f"{name}.log"
    if not path.exists():
        raise DeploymentError(f"no logs found for component: {name}")
    command = ["tail", "-n", str(lines)]
    if follow:
        command.append("-f")
    command.append(str(path))
    return subprocess.call(command)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stashseek")
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{init,start,stop,restart,status,logs}",
    )
    init = commands.add_parser("init", help="create a minimal private configuration")
    init.add_argument("--profile", choices=PROFILES, default="full")
    init.add_argument("--force", action="store_true")
    start_cmd = commands.add_parser("start", help="prepare and start the selected runtime")
    start_cmd.add_argument("--profile", choices=PROFILES)
    start_cmd.add_argument("--foreground", action="store_true")
    commands.add_parser("stop")
    restart = commands.add_parser("restart")
    restart.add_argument("--profile", choices=PROFILES)
    restart.add_argument("--foreground", action="store_true")
    commands.add_parser("status")
    log_cmd = commands.add_parser("logs")
    log_cmd.add_argument("component", nargs="?")
    log_cmd.add_argument("--follow", "-f", action="store_true")
    log_cmd.add_argument("--lines", type=int, default=100)
    supervise_cmd = commands.add_parser("supervise")
    supervise_cmd.add_argument("--profile", choices=PROFILES, required=True)
    supervise_cmd.add_argument("--run-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.profile, force=args.force)
        elif args.command == "start":
            start(args.profile, foreground=args.foreground)
        elif args.command == "stop":
            stop()
        elif args.command == "restart":
            stop()
            start(args.profile, foreground=args.foreground)
        elif args.command == "status":
            return status()
        elif args.command == "logs":
            if args.lines < 1 or args.lines > 10000:
                raise DeploymentError("--lines must be between 1 and 10000")
            return logs(args.component, follow=args.follow, lines=args.lines)
        elif args.command == "supervise":
            return supervise(args.profile, args.run_id)
    except DeploymentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
