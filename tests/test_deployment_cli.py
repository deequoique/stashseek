from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from app import deployment


def test_read_profile_needs_only_postgres_and_mcp():
    plan = deployment.build_plan("read", {})
    assert plan.compose_services == ("postgres",)
    assert plan.components == ("mcp",)


def test_full_profile_includes_mcp_gateway_one_worker_and_one_beat():
    plan = deployment.build_plan("full", {})
    assert plan.compose_services == ("postgres", "redis", "minio")
    assert plan.components == ("worker", "beat", "mcp", "gateway")
    commands = deployment._component_commands("full")
    assert tuple(commands) == ("worker", "beat", "mcp", "gateway")
    assert "--queues=ingest,maintenance" in commands["worker"]
    assert "--concurrency=1" in commands["worker"]
    assert "--schedule=.runtime/deployment/celerybeat-schedule" in commands["beat"]


def test_remote_dependencies_are_not_managed():
    plan = deployment.build_plan(
        "full",
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/kb",
            "REDIS_URL": "rediss://redis.example.invalid/0",
            "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        },
    )
    assert plan.compose_services == ()


def test_split_remote_hosts_are_not_managed():
    plan = deployment.build_plan(
        "full",
        {
            "POSTGRES_HOST": "db.example.invalid",
            "REDIS_HOST": "redis.example.invalid",
            "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        },
    )
    assert plan.compose_services == ()


def test_local_service_classification_respects_scheme_and_port_overrides():
    local = deployment.build_plan(
        "full",
        {
            "REDIS_URL": "redis://localhost:6380/0",
            "REDIS_PORT": "6380",
            "MINIO_ENDPOINT_URL": "http://localhost:9100",
            "MINIO_API_PORT": "9100",
        },
    )
    assert local.compose_services == ("postgres", "redis", "minio")
    secure_loopback = deployment.build_plan(
        "full",
        {
            "REDIS_URL": "rediss://localhost:6379/0",
            "MINIO_ENDPOINT_URL": "https://localhost:9000",
        },
    )
    assert secure_loopback.compose_services == ("postgres",)


def test_invalid_service_url_fails_without_echoing_value():
    secret_url = "not-a-url-with-secret"
    with pytest.raises(deployment.DeploymentError) as error:
        deployment.build_plan("full", {"REDIS_URL": secret_url})
    assert secret_url not in str(error.value)


def test_environment_precedence_process_then_operator_then_generated(tmp_path):
    generated = tmp_path / ".env.runtime"
    generated.write_text("A=generated\nB=generated\n", encoding="utf-8")
    operator = tmp_path / ".env"
    operator.write_text("B=operator\nC=operator\n", encoding="utf-8")
    resolved = deployment.load_environment(
        {"C": "process", "D": "process"},
        managed_path=generated,
        operator_path=operator,
    )
    assert resolved == {
        "A": "generated",
        "B": "operator",
        "C": "process",
        "D": "process",
    }


def test_auto_init_profile_uses_operator_or_process_precedence(monkeypatch, tmp_path):
    operator = tmp_path / ".env"
    operator.write_text("NOTEBOOK_AGENT_PROFILE=langbot\n", encoding="utf-8")
    monkeypatch.setattr(deployment, "OPERATOR_ENV", operator)
    monkeypatch.delenv("NOTEBOOK_AGENT_PROFILE", raising=False)
    assert deployment._auto_init_profile(None) == "langbot"
    monkeypatch.setenv("NOTEBOOK_AGENT_PROFILE", "read")
    assert deployment._auto_init_profile(None) == "read"
    assert deployment._auto_init_profile("full") == "full"


def test_private_env_is_minimal_private_and_rejects_newlines(tmp_path):
    path = tmp_path / ".env.runtime"
    deployment._write_private_env(path, {"NOTEBOOK_AGENT_PROFILE": "read", "TOKEN": "secret"})
    assert path.read_text(encoding="utf-8").splitlines() == [
        "NOTEBOOK_AGENT_PROFILE='read'",
        "TOKEN='secret'",
    ]
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(deployment.DeploymentError, match="single-line"):
        deployment._write_private_env(path, {"TOKEN": "secret\nleak"})


def test_generated_secret_does_not_interpolate_dollar_syntax(tmp_path):
    generated = tmp_path / ".env.runtime"
    deployment._write_private_env(generated, {"TOKEN": "a${UNSET}b"})
    assert deployment.load_environment(
        {}, managed_path=generated, operator_path=tmp_path / ".env"
    )["TOKEN"] == "a${UNSET}b"


def test_force_reprofile_preserves_generated_database_secret(
    monkeypatch, tmp_path
):
    managed = tmp_path / ".env.runtime"
    managed.write_text(
        "NOTEBOOK_AGENT_PROFILE='read'\n"
        "POSTGRES_PASSWORD='stable-password'\n"
        "ZHIPU_API_KEY='embedding'\n"
        "AGENT_API_KEY='agent'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deployment, "MANAGED_ENV", managed)
    monkeypatch.setattr(deployment, "OPERATOR_ENV", tmp_path / ".env")
    # This test exercises generated local MinIO credentials. Other tests may
    # have loaded the operator .env into the process environment already.
    for name in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ENDPOINT_URL",
        "MINIO_API_PORT",
        "CHANNEL_GATEWAY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    deployment.initialize("full", force=True)
    values = deployment.load_environment(
        {}, managed_path=managed, operator_path=tmp_path / ".env"
    )
    assert values["POSTGRES_PASSWORD"] == "stable-password"
    assert values["NOTEBOOK_AGENT_PROFILE"] == "full"
    assert values["MINIO_ROOT_PASSWORD"]
    assert len(values["CHANNEL_GATEWAY_SECRET"]) >= 32
    gateway_secret = values["CHANNEL_GATEWAY_SECRET"]
    deployment.initialize("full", force=True)
    reloaded = deployment.load_environment(
        {}, managed_path=managed, operator_path=tmp_path / ".env"
    )
    assert reloaded["CHANNEL_GATEWAY_SECRET"] == gateway_secret


def test_required_variables_are_profile_specific():
    assert deployment.required_variables("read", {}) == (
        "ZHIPU_API_KEY",
        "POSTGRES_PASSWORD",
    )
    missing = deployment.required_variables("langbot", {})
    assert "MINIO_ROOT_PASSWORD" in missing
    assert "CHANNEL_GATEWAY_SECRET" in missing
    assert "CHANNEL_GATEWAY_SECRET" in deployment.required_variables("full", {})


def test_prepare_refuses_pooled_neon_migration_without_direct_url(monkeypatch):
    env = {
        "DATABASE_URL": "postgresql://role:redacted@host-pooler.example.neon.tech/kb?sslmode=require",
        "ZHIPU_API_KEY": "embedding",
        "AGENT_API_KEY": "agent",
    }
    calls = []
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda *args, **_kwargs: calls.append(args),
    )
    with pytest.raises(deployment.DeploymentError, match="MIGRATION_DATABASE_URL"):
        deployment._prepare("read", env)
    assert calls == []


def test_migration_url_must_be_matching_direct_tls_neon_host():
    runtime = "postgresql://role:secret@ep-demo-pooler.aws.neon.tech/kb?sslmode=require"
    with pytest.raises(deployment.DeploymentError, match="direct database host"):
        deployment._migration_environment(
            {
                "DATABASE_URL": runtime,
                "MIGRATION_DATABASE_URL": runtime,
            }
        )
    with pytest.raises(deployment.DeploymentError, match="runtime database"):
        deployment._migration_environment(
            {
                "DATABASE_URL": runtime,
                "MIGRATION_DATABASE_URL": "postgresql://role:secret@ep-other.aws.neon.tech/kb?sslmode=require",
            }
        )
    with pytest.raises(deployment.DeploymentError, match="database name"):
        deployment._migration_environment(
            {
                "DATABASE_URL": runtime,
                "MIGRATION_DATABASE_URL": "postgresql://role:secret@ep-demo.aws.neon.tech/other?sslmode=require",
            }
        )
    migration = "postgresql://role:secret@ep-demo.aws.neon.tech/kb?sslmode=require"
    result = deployment._migration_environment(
        {"DATABASE_URL": runtime, "MIGRATION_DATABASE_URL": migration}
    )
    assert result["DATABASE_URL"] == migration
    assert "MIGRATION_DATABASE_URL" not in result


def test_pooled_neon_runtime_must_require_tls():
    with pytest.raises(deployment.DeploymentError, match="require TLS"):
        deployment._migration_environment(
            {
                "DATABASE_URL": "postgresql://role:secret@ep-demo-pooler.aws.neon.tech/kb",
                "MIGRATION_DATABASE_URL": "postgresql://role:secret@ep-demo.aws.neon.tech/kb?sslmode=require",
            }
        )


def test_nonpooled_migration_url_must_match_runtime_target():
    runtime = "postgresql://app:secret@db.example.invalid/prod"
    with pytest.raises(deployment.DeploymentError, match="database name"):
        deployment._migration_environment(
            {
                "DATABASE_URL": runtime,
                "MIGRATION_DATABASE_URL": "postgresql://admin:secret@db.example.invalid/dev",
            }
        )
    with pytest.raises(deployment.DeploymentError, match="database host"):
        deployment._migration_environment(
            {
                "DATABASE_URL": runtime,
                "MIGRATION_DATABASE_URL": "postgresql://admin:secret@other.example.invalid/prod",
            }
        )
    with pytest.raises(deployment.DeploymentError, match="explicit DATABASE_URL"):
        deployment._migration_environment(
            {"MIGRATION_DATABASE_URL": "postgresql://admin:secret@db.example.invalid/prod"}
        )


def test_prepare_rolls_back_only_new_local_services(monkeypatch):
    env = {
        "ZHIPU_API_KEY": "embedding",
        "POSTGRES_PASSWORD": "database",
        "MINIO_ROOT_USER": "storage",
        "MINIO_ROOT_PASSWORD": "storage-secret",
        "CHANNEL_GATEWAY_SECRET": "g" * 32,
    }
    calls = []
    monkeypatch.setattr(deployment, "_running_compose_services", lambda _env: {"postgres"})

    def run(command, _env, **_kwargs):
        calls.append(command)
        if command[1:4] == ["-m", "alembic", "upgrade"]:
            raise deployment.DeploymentError("migration failed")

    monkeypatch.setattr(deployment, "_run_checked", run)
    with pytest.raises(deployment.DeploymentError, match="migration failed"):
        deployment._prepare("full", env)
    assert calls[-1] == ["docker", "compose", "stop", "redis", "minio"]


def test_compose_ownership_probe_failure_is_fail_closed(monkeypatch):
    def fail(*_args, **_kwargs):
        raise deployment.subprocess.CalledProcessError(1, ["docker", "compose", "ps"])

    monkeypatch.setattr(deployment.subprocess, "run", fail)
    with pytest.raises(deployment.DeploymentError, match="ownership"):
        deployment._running_compose_services({})


def test_prepare_requires_acknowledgement_for_non_loopback_mcp(monkeypatch):
    calls = []
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda *args, **_kwargs: calls.append(args),
    )
    env = {
        "DATABASE_URL": "postgresql://role:secret@db.example.invalid/kb",
        "ZHIPU_API_KEY": "embedding",
        "MCP_HOST": "0.0.0.0",
    }
    with pytest.raises(deployment.DeploymentError, match="NON_LOOPBACK"):
        deployment._prepare("read", env)
    assert calls == []


def test_prepare_does_not_create_external_object_bucket(monkeypatch):
    calls = []
    env = {
        "DATABASE_URL": "postgresql://role:secret@db.example.invalid/kb",
        "REDIS_URL": "rediss://redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        "MINIO_ROOT_USER": "storage",
        "MINIO_ROOT_PASSWORD": "storage-secret",
        "CHANNEL_GATEWAY_SECRET": "g" * 32,
        "ZHIPU_API_KEY": "embedding",
    }
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda command, _env, **_kwargs: calls.append(command),
    )
    deployment._prepare("full", env)
    assert not any("RawObjectStore" in " ".join(command) for command in calls)


@pytest.mark.parametrize("profile", ["full", "langbot"])
def test_gateway_profiles_reject_non_loopback_before_side_effects(
    monkeypatch, profile
):
    env = {
        "DATABASE_URL": "postgresql://role:secret@db.example.invalid/kb",
        "REDIS_URL": "rediss://redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        "MINIO_ROOT_USER": "storage",
        "MINIO_ROOT_PASSWORD": "storage-secret",
        "CHANNEL_GATEWAY_SECRET": "g" * 32,
        "CHANNEL_GATEWAY_HOST": "0.0.0.0",
        "ZHIPU_API_KEY": "embedding",
    }
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda *_args, **_kwargs: pytest.fail("must validate before side effects"),
    )
    with pytest.raises(deployment.DeploymentError, match="loopback"):
        deployment._prepare(profile, env)


@pytest.mark.parametrize("profile", ["full", "langbot"])
def test_gateway_profiles_reject_short_secret_before_side_effects(
    monkeypatch, profile
):
    env = {
        "DATABASE_URL": "postgresql://role:secret@db.example.invalid/kb",
        "REDIS_URL": "rediss://redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        "MINIO_ROOT_USER": "storage",
        "MINIO_ROOT_PASSWORD": "storage-secret",
        "CHANNEL_GATEWAY_SECRET": "short",
        "ZHIPU_API_KEY": "embedding",
    }
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda *_args, **_kwargs: pytest.fail("must validate before side effects"),
    )
    with pytest.raises(deployment.DeploymentError, match="at least 32"):
        deployment._prepare(profile, env)


def test_full_rejects_listener_port_collision_before_side_effects(monkeypatch):
    env = {
        "DATABASE_URL": "postgresql://role:secret@db.example.invalid/kb",
        "REDIS_URL": "rediss://redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        "MINIO_ROOT_USER": "storage",
        "MINIO_ROOT_PASSWORD": "storage-secret",
        "CHANNEL_GATEWAY_SECRET": "g" * 32,
        "MCP_PORT": "8765",
        "CHANNEL_GATEWAY_PORT": "8765",
        "ZHIPU_API_KEY": "embedding",
    }
    monkeypatch.setattr(
        deployment,
        "_run_checked",
        lambda *_args, **_kwargs: pytest.fail("must validate before side effects"),
    )

    with pytest.raises(deployment.DeploymentError, match="distinct ports"):
        deployment._prepare("full", env)


def test_active_state_requires_matching_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(deployment, "STATE_FILE", tmp_path / "runtime.json")
    deployment.STATE_FILE.write_text(
        '{"supervisor_pid": 123, "run_id": "safe-run", "profile": "full"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(deployment, "_pid_matches", lambda pid, run_id: False)
    assert deployment._active_state() is None
    monkeypatch.setattr(deployment, "_pid_matches", lambda pid, run_id: (pid, run_id) == (123, "safe-run"))
    assert deployment._active_state()["profile"] == "full"


def test_claimed_reservation_and_matching_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(deployment, "STATE_DIR", tmp_path)
    monkeypatch.setattr(deployment, "STATE_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(deployment, "LOCK_FILE", tmp_path / "lifecycle.lock")
    deployment.STATE_FILE.write_text(
        '{"run_id":"first","profile":"full","phase":"starting","launcher_pid":123}',
        encoding="utf-8",
    )
    monkeypatch.setattr(deployment, "_launcher_matches", lambda pid: pid == 123)
    deployment._claim_reservation("first", "full")
    state = deployment._read_state()
    assert state["phase"] == "supervising"
    assert state["supervisor_pid"] == os.getpid()
    deployment.STATE_FILE.write_text(
        '{"run_id":"second","profile":"read","phase":"starting","launcher_pid":456}',
        encoding="utf-8",
    )
    deployment._remove_state("first")
    assert deployment._read_state()["run_id"] == "second"


def test_status_never_prints_environment_values(monkeypatch, capsys):
    monkeypatch.setattr(
        deployment,
        "_active_state",
        lambda: {
            "profile": "full",
            "children": {"worker": 10, "beat": 11},
            "managed_services": [],
            "health_target_fingerprint": deployment._health_target_fingerprint(
                "full", {}
            ),
        },
    )
    monkeypatch.setattr(
        deployment,
        "_child_pid_matches",
        lambda _name, _pid: True,
    )
    monkeypatch.setattr(deployment, "load_environment", lambda: {})
    monkeypatch.setattr(
        deployment,
        "_full_runtime_checks",
        lambda _env: {"database": True, "broker": False},
    )
    monkeypatch.setattr(deployment, "_compose_health", lambda *_args: {})
    monkeypatch.setattr(deployment, "_port_ready", lambda *_args: True)
    monkeypatch.setenv("AGENT_API_KEY", "must-not-appear")
    assert deployment.status() == 1
    output = capsys.readouterr().out
    assert "must-not-appear" not in output
    assert "process.worker: ready" in output
    assert "dependency.database: ready" in output
    assert "dependency.broker: unavailable" in output
    assert "listener.mcp: ready" in output
    assert "listener.gateway: ready" in output


@pytest.mark.parametrize(
    ("profile", "expected_port"), (("read", 8000), ("langbot", 8765))
)
def test_single_listener_status_keeps_compatible_label(
    monkeypatch, capsys, profile, expected_port
):
    env = {}
    monkeypatch.setattr(
        deployment,
        "_active_state",
        lambda: {
            "profile": profile,
            "children": {},
            "managed_services": [],
            "health_target_fingerprint": deployment._health_target_fingerprint(
                profile, env
            ),
        },
    )
    monkeypatch.setattr(deployment, "load_environment", lambda: env)
    monkeypatch.setattr(deployment, "_database_ready", lambda _env: True)
    monkeypatch.setattr(
        deployment,
        "_full_runtime_checks",
        lambda _env: {name: True for name in deployment.FULL_DEPENDENCY_NAMES},
    )
    monkeypatch.setattr(deployment, "_compose_health", lambda *_args: {})
    checked_ports = []
    monkeypatch.setattr(
        deployment,
        "_port_ready",
        lambda _host, port: checked_ports.append(port) or True,
    )

    assert deployment.status() == 0
    output = capsys.readouterr().out
    assert "listener: ready" in output
    assert "listener.mcp" not in output and "listener.gateway" not in output
    assert checked_ports == [expected_port]


def test_status_refuses_to_probe_a_different_runtime_target(monkeypatch, capsys):
    runtime_env = {
        "DATABASE_URL": "postgresql://role@runtime.example.invalid/notebook",
        "REDIS_URL": "rediss://runtime-redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://runtime-objects.example.invalid",
    }
    status_env = {
        "DATABASE_URL": "postgresql://role@other.example.invalid/notebook",
        "REDIS_URL": "rediss://other-redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://other-objects.example.invalid",
    }
    monkeypatch.setattr(
        deployment,
        "_active_state",
        lambda: {
            "profile": "full",
            "children": {},
            "managed_services": [],
            "health_target_fingerprint": deployment._health_target_fingerprint(
                "full", runtime_env
            ),
        },
    )
    monkeypatch.setattr(deployment, "load_environment", lambda: status_env)
    monkeypatch.setattr(
        deployment,
        "_full_runtime_checks",
        lambda _env: pytest.fail("must not probe a different dependency target"),
    )
    monkeypatch.setattr(
        deployment,
        "_compose_health",
        lambda *_args: pytest.fail("must not probe a different Compose target"),
    )
    monkeypatch.setattr(
        deployment,
        "_port_ready",
        lambda *_args: pytest.fail("must not probe a different listener target"),
    )

    assert deployment.status() == 1
    assert "configuration.runtime: unavailable" in capsys.readouterr().out


def test_health_target_fingerprint_excludes_passwords_but_detects_targets():
    first = {
        "DATABASE_URL": "postgresql://role:first@db.example.invalid/kb",
        "REDIS_URL": "rediss://default:first@redis.example.invalid/0",
        "MINIO_ENDPOINT_URL": "https://objects.example.invalid",
        "MINIO_ROOT_PASSWORD": "first",
    }
    rotated = {
        **first,
        "DATABASE_URL": "postgresql://role:second@db.example.invalid/kb",
        "REDIS_URL": "rediss://default:second@redis.example.invalid/0",
        "MINIO_ROOT_PASSWORD": "second",
    }
    different_target = {
        **rotated,
        "DATABASE_URL": "postgresql://role:second@other.example.invalid/kb",
    }
    different_mcp = {**rotated, "MCP_PORT": "9000"}
    different_gateway = {**rotated, "CHANNEL_GATEWAY_PORT": "9001"}

    assert deployment._health_target_fingerprint("full", first) == (
        deployment._health_target_fingerprint("full", rotated)
    )
    assert deployment._health_target_fingerprint("full", rotated) != (
        deployment._health_target_fingerprint("full", different_target)
    )
    assert deployment._health_target_fingerprint("full", rotated) != (
        deployment._health_target_fingerprint("full", different_mcp)
    )
    assert deployment._health_target_fingerprint("full", rotated) != (
        deployment._health_target_fingerprint("full", different_gateway)
    )


def test_compose_health_is_redacted(monkeypatch):
    class Result:
        stdout = (
            '{"Service":"postgres","State":"running","Health":"healthy"}\n'
            '{"Service":"redis","State":"running","Health":"healthy"}\n'
        )

    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(deployment.subprocess, "run", run)
    env = {
        "POSTGRES_PASSWORD": "database-secret",
        "MINIO_ROOT_USER": "storage-user",
        "MINIO_ROOT_PASSWORD": "storage-secret",
    }
    result = deployment._compose_health(("postgres", "redis", "minio"), env)
    assert result == {"postgres": True, "redis": True, "minio": False}
    assert "database-secret" not in " ".join(captured["command"])


def test_checked_subprocess_failure_does_not_echo_captured_secret(monkeypatch):
    secret = "postgresql://role:very-secret@example.invalid/kb"

    def fail(*_args, **_kwargs):
        raise deployment.subprocess.CalledProcessError(
            1, ["alembic"], stderr=secret
        )

    monkeypatch.setattr(deployment.subprocess, "run", fail)
    with pytest.raises(deployment.DeploymentError) as error:
        deployment._run_checked(["alembic", "upgrade", "head"], {})
    assert secret not in str(error.value)


def test_component_log_rotates_and_redacts_configured_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(deployment, "LOG_DIR", tmp_path)
    secret = "log-secret-value"
    short_secret = "x"
    overlapping_secret = f"{secret}-suffix"
    capture = deployment._ComponentLog(
        "worker",
        io.StringIO(
            f"broker={secret} longer={overlapping_secret} short={short_secret}\nready\n"
        ),
        {
            "REDIS_URL": secret,
            "AGENT_API_KEY": short_secret,
            "CHANNEL_GATEWAY_SECRET": overlapping_secret,
            "MINIO_ROOT_USER": "storage-user",
            "AGENT_BASE_URL": "https://user:password@example.invalid/v1",
            "NOTEBOOK_AGENT_LOG_MAX_BYTES": "1024",
            "NOTEBOOK_AGENT_LOG_BACKUP_COUNT": "1",
        },
    )
    capture.close()
    output = (tmp_path / "worker.log").read_text(encoding="utf-8")
    assert secret not in output
    assert overlapping_secret not in output
    assert "short=x" not in output
    assert "broker=[REDACTED]" in output


def test_invalid_log_configuration_is_rejected_before_spawn(monkeypatch):
    monkeypatch.setattr(
        deployment.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("must validate before spawning"),
    )
    with pytest.raises(deployment.DeploymentError, match="log rotation"):
        deployment._spawn_component(
            "worker", ["celery"], {"NOTEBOOK_AGENT_LOG_MAX_BYTES": "1"}, []
        )


def test_untracked_component_is_killed_and_reaped_after_timeout(monkeypatch):
    class Child:
        pid = 42

        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise deployment.subprocess.TimeoutExpired(["worker"], timeout)
            return -9

    child = Child()
    signals = []
    monkeypatch.setattr(
        deployment,
        "_signal_child_group",
        lambda _child, sig: signals.append(sig),
    )
    deployment._stop_untracked_child(child)
    assert signals == [deployment.signal.SIGTERM, deployment.signal.SIGKILL]
    assert child.waits == 2


def test_start_installs_signal_forwarding_before_foreground_spawn(
    monkeypatch, tmp_path
):
    managed = tmp_path / ".env.runtime"
    managed.write_text("NOTEBOOK_AGENT_PROFILE=read\n", encoding="utf-8")
    monkeypatch.setattr(deployment, "MANAGED_ENV", managed)
    monkeypatch.setattr(deployment, "STATE_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(deployment, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(deployment, "load_environment", lambda: {})
    monkeypatch.setattr(deployment, "_prepare", lambda *_args: None)
    monkeypatch.setattr(deployment, "_write_reservation", lambda *_args: None)
    monkeypatch.setattr(deployment.secrets, "token_hex", lambda _size: "run-id")
    states = iter(
        [None, {"run_id": "run-id", "phase": "running", "profile": "read"}]
    )
    monkeypatch.setattr(deployment, "_active_state", lambda: next(states))

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(deployment, "_LifecycleLock", Lock)
    handlers = {}

    def install(signum, handler):
        previous = handlers.get(signum, deployment.signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(deployment.signal, "signal", install)

    class Process:
        def __init__(self):
            self.forwarded = []

        def poll(self):
            return None

        def send_signal(self, signum):
            self.forwarded.append(signum)

        def wait(self, timeout=None):
            return 0

    process = Process()

    def spawn(*_args, **_kwargs):
        assert deployment.signal.SIGTERM in handlers
        assert deployment.signal.SIGINT in handlers
        handlers[deployment.signal.SIGTERM](deployment.signal.SIGTERM, None)
        return process

    monkeypatch.setattr(deployment.subprocess, "Popen", spawn)
    with pytest.raises(SystemExit) as stopped:
        deployment.start("read", foreground=True)
    assert stopped.value.code == 0
    assert process.forwarded == [deployment.signal.SIGTERM]


def test_startup_waits_are_cancellable():
    with pytest.raises(deployment.DeploymentError, match="interrupted"):
        deployment._wait_for_listener(
            "read", {}, {}, should_stop=lambda: True
        )


def test_full_startup_waits_for_mcp_and_gateway_listeners(monkeypatch):
    calls = []

    def port_ready(_host, port):
        calls.append(port)
        return port == 8000 or calls.count(8765) >= 2

    monkeypatch.setattr(deployment, "_port_ready", port_ready)
    monkeypatch.setattr(deployment.time, "sleep", lambda _seconds: None)
    deployment._wait_for_listener("full", {}, {})
    assert calls == [8000, 8765, 8000, 8765]


def test_full_runtime_check_budget_covers_remote_probes(monkeypatch):
    captured = {}

    class Result:
        stdout = (
            '{"broker": true, "database": true, "maintenance": true, '
            '"object_store": true, "worker": true}'
        )

    def run(*_args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return Result()

    monkeypatch.setattr(deployment.subprocess, "run", run)
    assert all(deployment._full_runtime_checks({}).values())
    assert captured["timeout"] == deployment.FULL_RUNTIME_CHECK_TIMEOUT_SECONDS
    assert deployment.FULL_RUNTIME_CHECK_TIMEOUT_SECONDS >= 30
    assert (
        deployment.STARTUP_WAIT_TIMEOUT_SECONDS
        > deployment.LISTENER_WAIT_TIMEOUT_SECONDS
    )
    assert deployment.STOP_WAIT_TIMEOUT_SECONDS > 10


def test_supervisor_stops_siblings_when_required_child_exits(monkeypatch, tmp_path):
    class Child:
        next_pid = 100

        def __init__(self, command, **_kwargs):
            self.command = command
            self.pid = Child.next_pid
            Child.next_pid += 1
            self.returncode = 7 if command == ["fails"] else None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    children = []
    events = []

    def create_child(_name, command, _env, _logs):
        events.append(f"spawn:{_name}")
        child = Child(command)
        children.append(child)
        return child

    monkeypatch.setattr(deployment, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(deployment, "STATE_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(
        deployment,
        "_component_commands",
        lambda _profile: {
            "worker": ["waits"],
            "beat": ["waits"],
            "mcp": ["fails"],
            "gateway": ["waits"],
        },
    )
    monkeypatch.setattr(deployment, "_spawn_component", create_child)
    monkeypatch.setattr(deployment, "_claim_reservation", lambda *_args: None)
    monkeypatch.setattr(deployment, "_write_state", lambda *_args: None)
    monkeypatch.setattr(
        deployment,
        "_wait_for_listener",
        lambda *_args, **_kwargs: events.append("listener-ready"),
    )
    monkeypatch.setattr(
        deployment,
        "_signal_child_group",
        lambda child, sig: child.terminate() if sig == deployment.signal.SIGTERM else child.kill(),
    )
    monkeypatch.setattr(deployment.signal, "signal", lambda *_args: None)
    assert deployment.supervise("full", "run-id") == 7
    assert children[0].terminated is True
    assert children[1].terminated is True
    assert children[3].terminated is True
    assert events[:5] == [
        "spawn:worker",
        "spawn:beat",
        "spawn:mcp",
        "spawn:gateway",
        "listener-ready",
    ]


def test_direct_supervisor_invocation_requires_reservation(monkeypatch, tmp_path):
    monkeypatch.setattr(deployment, "STATE_DIR", tmp_path)
    monkeypatch.setattr(deployment, "STATE_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(deployment, "LOCK_FILE", tmp_path / "lifecycle.lock")
    with pytest.raises(deployment.DeploymentError, match="reservation"):
        deployment.supervise("full", "unreserved-run")


def test_stop_waits_for_starting_reservation_to_be_claimed(monkeypatch):
    states = iter(
        [
            {"phase": "starting", "launcher_pid": 10},
            {"phase": "supervising", "supervisor_pid": 20},
        ]
    )
    monkeypatch.setattr(deployment, "_active_state", lambda: next(states))
    monkeypatch.setattr(deployment.time, "sleep", lambda _seconds: None)
    alive = iter([False, False])
    monkeypatch.setattr(deployment, "_pid_alive", lambda _pid: next(alive))
    signals = []
    monkeypatch.setattr(deployment.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    deployment.stop()
    assert signals == [(20, deployment.signal.SIGTERM)]


def test_start_is_idempotent_before_prepare(monkeypatch, capsys):
    monkeypatch.setattr(deployment, "MANAGED_ENV", Path("/already-configured"))
    monkeypatch.setattr(deployment.Path, "exists", lambda self: True)
    monkeypatch.setattr(deployment, "load_environment", lambda: {})
    monkeypatch.setattr(
        deployment,
        "_active_state",
        lambda: {"profile": "read", "supervisor_pid": os.getpid(), "run_id": "run"},
    )
    monkeypatch.setattr(deployment, "_prepare", lambda *_args: pytest.fail("must not prepare twice"))
    deployment.start(None, foreground=False)
    assert "already running" in capsys.readouterr().out


def test_first_run_docs_load_launcher_environment_for_operator_cli():
    root = Path(__file__).parents[1]
    tutorial = (root / "docs/tutorials/first-run.md").read_text(encoding="utf-8")
    mcp_guide = (root / "docs/how-to/connect-mcp-client.md").read_text(
        encoding="utf-8"
    )

    assert ".venv/bin/dotenv -f .env.runtime run --no-override" in tutorial
    assert "notebook_run .venv/bin/python -m app.cli users create" in tutorial
    assert "notebook_run .venv/bin/python -m app.cli mcp-grant issue" in tutorial
    assert '"command": "/absolute/path/to/notebook-agent/.venv/bin/dotenv"' in mcp_guide
