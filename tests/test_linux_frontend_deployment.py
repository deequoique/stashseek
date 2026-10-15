from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_only_runtime_has_no_static_web_server_artifacts():
    assert not (ROOT / "deploy/nginx/notebook-agent-web.conf").exists()
    assert not (ROOT / "deploy/nginx/notebook-agent-web-security-headers.conf").exists()
    unit = (ROOT / "deploy/systemd/notebook-agent.service").read_text()
    assert "WEB_SERVE_STATIC=true" not in unit
    assert "web/dist" not in unit


def test_extension_is_the_distributed_client():
    package = (ROOT / "extension/package.json").read_text()
    assert '"build"' in package
    assert '"build:local"' in package
    assert '"audit"' in package
    assert not (ROOT / "web").exists()
