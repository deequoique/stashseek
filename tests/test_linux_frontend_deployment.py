from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_no_separate_static_web_server_artifacts_exist():
    # The SPA is served by the combined ASGI process, never by a second server.
    assert not (ROOT / "deploy/nginx/notebook-agent-web.conf").exists()
    assert not (ROOT / "deploy/nginx/notebook-agent-web-security-headers.conf").exists()


def test_runtime_unit_serves_the_extension_build_as_the_web_app():
    unit = _text("deploy/systemd/notebook-agent.service")
    for line in (
        "Environment=WEB_SERVE_STATIC=true",
        "Environment=WEB_STATIC_DIR=extension/dist",
        "Environment=WEB_COOKIE_SECURE=true",
        "Environment=WEB_FORWARDED_ALLOW_IPS=127.0.0.1",
    ):
        assert line in unit.splitlines()
    # The legacy web/ project is gone; web/dist exists only as a release-dir link.
    assert "web/dist" not in unit
    # The public origin is host-owned and must not be committed to the unit.
    assert "WEB_PUBLIC_ORIGIN" not in unit


def test_extension_is_the_distributed_client():
    package = _text("extension/package.json")
    assert '"build"' in package
    assert '"build:local"' in package
    assert '"audit"' in package
    assert not (ROOT / "web").exists()


def test_extension_build_emits_the_web_entry_next_to_the_extension_pages():
    index = _text("extension/index.html")
    assert '<script type="module" src="/src/main.tsx"></script>' in index
    assert 'name="viewport"' in index
    # The Web entry and the extension workspace share one application module.
    assert '/src/main.tsx' in _text("extension/workspace.html")
    vite = _text("extension/vite.config.ts")
    assert 'input: ["index.html", "workspace.html", "sidepanel.html"]' in vite


def test_deploy_builds_the_frontend_before_the_release_is_marked_built():
    script = _text("deploy/scripts/notebook-agent-deploy")

    chdir = '/usr/bin/env --chdir="$release_dir/extension"'
    assert script.count(f"runuser -u notebook-agent -- {chdir}") == 2
    pip = '-m pip install --quiet -e "$release_dir"'
    install = "corepack pnpm install --frozen-lockfile"
    build = "corepack pnpm build"
    link = 'ln -sfn ../extension/dist "$release_dir/web/dist"'
    marker = 'touch "$release_dir/.release-built"'
    switch = 'mv -Tf /opt/notebook-agent/current.next "$current"'
    assert (
        script.index(pip)
        < script.index(install)
        < script.index(build)
        < script.index(link)
        < script.index(marker)
        < script.index(switch)
    )
    # A failed build must abort the deployment, so no command may swallow errors.
    assert "set -euo pipefail" in script
    build_block = script[script.index(install) : script.index(marker)]
    assert "|| true" not in build_block
    assert 'mkdir -p "$release_dir/web"' in script
