"""US video egress: one loopback proxy setting for every server-side video fetch."""

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.connectors.base import TransientFetchError
from app.connectors.bilibili import BilibiliConnector
from app.connectors.proxy_env import build_proxy_environment, classify_proxy_failure
from app.connectors.youtube import YouTubeConnector
from app.ingest.tasks import _connector

ROOT = Path(__file__).resolve().parents[1]
HOME_EGRESS_HELPER = ROOT / "scripts" / "youtube-home-egress"
EGRESS_DIR = ROOT / "deploy" / "youtube-egress"
PROXY = "http://127.0.0.1:18080"
BILIBILI_ID = "BV1xx411c7mD"
EXPECTED_FILTER_RULES = [
    r"(^|\.)youtube\.com$",
    r"(^|\.)googlevideo\.com$",
    r"(^|\.)bilibili\.com$",
    r"(^|\.)b23\.tv$",
    r"(^|\.)hdslb\.com$",
    r"(^|\.)bilivideo\.com$",
    r"(^|\.)bilivideo\.cn$",
    r"^upos-[a-z0-9-]+\.akamaized\.net$",
]


# --- Settings: US_VIDEO_PROXY_URL replaces YOUTUBE_PROXY_URL ------------------


def test_us_video_proxy_url_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("YOUTUBE_PROXY_URL", raising=False)
    monkeypatch.delenv("US_VIDEO_PROXY_URL", raising=False)
    assert Settings().us_video_proxy_url is None

    monkeypatch.setenv("US_VIDEO_PROXY_URL", PROXY)
    assert Settings().us_video_proxy_url == PROXY


@pytest.mark.parametrize(
    "proxy_url",
    (
        "",
        " http://127.0.0.1:18080",
        "https://127.0.0.1:18080",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://localhost:18080/",
        "http://localhost:18080/path",
        "http://localhost:18080?mode=connect",
        "http://localhost:18080#fragment",
        "http://user:password@localhost:18080",
        "http://proxy.example:18080",
        "http://[::1]:18080",
    ),
)
def test_us_video_proxy_url_rejects_unsafe_values_naming_the_new_key(
    monkeypatch, proxy_url
):
    monkeypatch.delenv("YOUTUBE_PROXY_URL", raising=False)
    monkeypatch.setenv("US_VIDEO_PROXY_URL", proxy_url)

    with pytest.raises(ValueError, match="US_VIDEO_PROXY_URL") as caught:
        Settings()

    assert "YOUTUBE_PROXY_URL" not in str(caught.value)
    if proxy_url:
        assert proxy_url not in str(caught.value)


@pytest.mark.parametrize("old_value", ("", PROXY, "http://proxy.example:18080"))
def test_old_youtube_proxy_key_fails_loudly_instead_of_aliasing(
    monkeypatch, old_value
):
    monkeypatch.setenv("YOUTUBE_PROXY_URL", old_value)
    monkeypatch.delenv("US_VIDEO_PROXY_URL", raising=False)

    with pytest.raises(ValueError) as caught:
        Settings()

    assert str(caught.value) == (
        "YOUTUBE_PROXY_URL was renamed to US_VIDEO_PROXY_URL; "
        "move the value and remove the old variable"
    )
    if old_value:
        assert old_value not in str(caught.value)


def test_old_youtube_proxy_key_is_rejected_even_with_the_new_key_set(monkeypatch):
    monkeypatch.setenv("YOUTUBE_PROXY_URL", PROXY)
    monkeypatch.setenv("US_VIDEO_PROXY_URL", PROXY)

    with pytest.raises(ValueError, match="was renamed to US_VIDEO_PROXY_URL"):
        Settings()
    with pytest.raises(ValueError, match="was renamed to US_VIDEO_PROXY_URL"):
        Settings(us_video_proxy_url=PROXY)


# --- Shared child-proxy helper ------------------------------------------------


def test_build_proxy_environment_without_proxy_returns_none():
    assert build_proxy_environment(None) is None


def test_build_proxy_environment_is_a_loopback_only_copy(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/verified/ca.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/verified/ca.pem")
    monkeypatch.setenv("ALL_PROXY", "socks5://ambient.invalid:1080")
    monkeypatch.setenv("all_proxy", "socks5://ambient.invalid:1080")
    monkeypatch.setenv("NO_PROXY", ".youtube.com,.bilibili.com")
    parent = dict(os.environ)

    environment = build_proxy_environment(PROXY)

    assert environment is not None
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert environment[name] == PROXY
    assert environment["NO_PROXY"] == environment["no_proxy"] == "127.0.0.1,localhost,::1"
    assert "ALL_PROXY" not in environment and "all_proxy" not in environment
    assert environment["SSL_CERT_FILE"] == environment["REQUESTS_CA_BUNDLE"] == "/verified/ca.pem"
    assert os.environ == parent
    assert environment is not os.environ


@pytest.mark.parametrize(
    ("stderr", "suffix"),
    (
        ("ERROR: HTTP Error 407: Proxy Authentication Required", "authentication_failed"),
        ("ERROR: proxy timed out", "timeout"),
        ("ERROR: Proxy Timeout while connecting", "timeout"),
        ("ProxyError: Unable to connect to proxy", "unavailable"),
        ("[Errno 61] Connection refused", "unavailable"),
    ),
)
@pytest.mark.parametrize("prefix", ("youtube", "bilibili"))
def test_classify_proxy_failure_uses_the_platform_prefix(prefix, stderr, suffix):
    assert classify_proxy_failure(stderr, prefix=prefix) == f"{prefix}_proxy_{suffix}"


@pytest.mark.parametrize("stderr", (None, "", "ERROR: Video unavailable", b"bytes"))
def test_classify_proxy_failure_returns_none_for_other_errors(stderr):
    assert classify_proxy_failure(stderr, prefix="bilibili") is None


def test_youtube_connector_no_longer_carries_its_own_proxy_logic():
    source = (ROOT / "app" / "connectors" / "youtube.py").read_text(encoding="utf-8")

    assert "_PROXY_AUTH_MARKERS" not in source
    assert "_build_proxy_environment" not in source
    assert "ALL_PROXY" not in source
    assert "from app.connectors.proxy_env import" in source


# --- Bilibili connector through the proxy -------------------------------------


def _metadata_runner(calls, *, stdout=None):
    payload = json.dumps({"id": BILIBILI_ID, "title": "A title", "subtitles": {}})

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0, stdout=payload if stdout is None else stdout, stderr=""
        )

    return runner


def _failing_runner(calls, stderr):
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    return runner


def test_bilibili_proxied_metadata_child_gets_the_child_only_environment(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/verified/ca.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/verified/ca.pem")
    monkeypatch.setenv("ALL_PROXY", "socks5://ambient.invalid:1080")
    monkeypatch.setenv("NO_PROXY", ".bilibili.com")
    parent = dict(os.environ)
    calls = []
    connector = BilibiliConnector(runner=_metadata_runner(calls), proxy_url=PROXY)

    connector.fetch_meta(BILIBILI_ID)
    connector.fetch_meta(BILIBILI_ID)

    first_env, second_env = calls[0][1]["env"], calls[1][1]["env"]
    for env in (first_env, second_env):
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            assert env[name] == PROXY
        assert env["NO_PROXY"] == env["no_proxy"] == "127.0.0.1,localhost,::1"
        assert "ALL_PROXY" not in env and "all_proxy" not in env
        assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == "/verified/ca.pem"
    assert first_env is not second_env  # a fresh copy per child
    assert os.environ == parent
    assert "HTTPS_PROXY" not in os.environ
    # Everything else about the command is unchanged and cookie-free.
    assert "--no-playlist" in calls[0][0] and "--skip-download" in calls[0][0]
    assert not any("cookie" in str(value).lower() for value in calls[0][0])


def test_bilibili_without_proxy_passes_no_env_and_inherits_the_worker(monkeypatch):
    calls = []
    connector = BilibiliConnector(runner=_metadata_runner(calls))

    connector.fetch_meta(BILIBILI_ID)

    assert "env" not in calls[0][1]


@pytest.mark.parametrize("proxy_url", (None, PROXY))
@pytest.mark.parametrize(
    "stderr",
    (
        "ERROR: [BiliBili] Unable to download webpage: HTTP Error 412: Precondition Failed",
        "Precondition Failed (caused by <HTTPError 412>)",
        "HTTP Error 429: Too Many Requests",
        "Unable to download video info: -352: 风控校验失败",
    ),
)
def test_bilibili_risk_control_and_412_are_rate_limited(proxy_url, stderr):
    calls = []
    connector = BilibiliConnector(
        runner=_failing_runner(calls, stderr), proxy_url=proxy_url
    )

    with pytest.raises(TransientFetchError, match="^bilibili_rate_limited$"):
        connector.fetch_meta(BILIBILI_ID)

    assert len(calls) == 1


@pytest.mark.parametrize(
    ("stderr", "classification"),
    (
        (
            (
                "unable to connect to proxy http://127.0.0.1:18080 fetching "
                "https://www.bilibili.com/video/BV1xx411c7mD?private-signed-query"
            ),
            "bilibili_proxy_unavailable",
        ),
        (
            "ProxyError: Connection refused by http://127.0.0.1:18080",
            "bilibili_proxy_unavailable",
        ),
        (
            # tinyproxy filter refusal (403 on CONNECT), as yt-dlp reports it.
            (
                "ERROR: [BiliBili] 1xx411c7mD: Unable to download webpage: "
                "('Unable to connect to proxy', OSError('Tunnel connection failed: "
                "403 Filtered')) private-signed-query"
            ),
            "bilibili_proxy_unavailable",
        ),
        (
            "HTTP Error 407: Proxy Authentication Required private-signed-query",
            "bilibili_proxy_authentication_failed",
        ),
        ("proxy timed out private-signed-query", "bilibili_proxy_timeout"),
    ),
)
def test_bilibili_proxy_failures_are_stable_redacted_and_never_retried_directly(
    stderr, classification
):
    calls = []
    connector = BilibiliConnector(
        runner=_failing_runner(calls, stderr), proxy_url=PROXY
    )

    with pytest.raises(TransientFetchError) as caught:
        connector.fetch_meta(BILIBILI_ID)

    assert str(caught.value) == classification
    assert "private-signed-query" not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)
    assert len(calls) == 1
    assert calls[0][1]["env"]["HTTPS_PROXY"] == PROXY  # the only attempt was proxied


def test_bilibili_proxy_markers_without_a_proxy_stay_a_generic_failure():
    connector = BilibiliConnector(
        runner=_failing_runner([], "unable to connect to proxy; connection refused")
    )

    with pytest.raises(TransientFetchError, match="^bilibili_fetch_failed$"):
        connector.fetch_meta(BILIBILI_ID)


def test_bilibili_video_id_containing_412_is_not_mistaken_for_http_412():
    connector = BilibiliConnector(
        runner=_failing_runner(
            [], "[BiliBili] BV1412abcdEF: unable to connect to proxy"
        ),
        proxy_url=PROXY,
    )

    with pytest.raises(TransientFetchError, match="^bilibili_proxy_unavailable$"):
        connector.fetch_meta("BV1412abcdEF")


@pytest.mark.parametrize(
    ("proxy_url", "classification"),
    ((PROXY, "bilibili_proxy_timeout"), (None, "bilibili_fetch_timeout")),
)
def test_bilibili_timeout_classification_depends_on_the_proxy(
    proxy_url, classification
):
    calls = []

    def timed_out(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    connector = BilibiliConnector(
        runner=timed_out, proxy_url=proxy_url, fetch_timeout_seconds=0.25
    )

    with pytest.raises(TransientFetchError) as caught:
        connector.fetch_meta(BILIBILI_ID)

    assert str(caught.value) == classification
    assert len(calls) == 1


def test_real_bilibili_child_fails_closed_when_loopback_proxy_is_absent():
    connector = BilibiliConnector(proxy_url="http://127.0.0.1:9", fetch_timeout_seconds=10)

    with pytest.raises(TransientFetchError, match="^bilibili_proxy_unavailable$"):
        connector.fetch_meta(BILIBILI_ID)


# --- Worker wiring ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "connector_type"),
    (
        ("https://www.bilibili.com/video/BV1xx411c7mD", BilibiliConnector),
        ("https://youtu.be/dQw4w9WgXcQ", YouTubeConnector),
    ),
)
def test_worker_connector_passes_the_us_video_proxy_to_both_platforms(
    monkeypatch, url, connector_type
):
    settings = replace(Settings(), us_video_proxy_url=PROXY)
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    monkeypatch.setattr("app.ingest.tasks.configure_trusted_ca", lambda _bundle: None)

    connector = _connector(url)

    assert isinstance(connector, connector_type)
    assert connector._proxy_url == PROXY
    assert connector._child_environment()["HTTPS_PROXY"] == PROXY


@pytest.mark.parametrize(
    "url",
    (
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://youtu.be/dQw4w9WgXcQ",
    ),
)
def test_worker_connector_is_direct_when_no_proxy_is_configured(monkeypatch, url):
    settings = replace(Settings(), us_video_proxy_url=None)
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    monkeypatch.setattr("app.ingest.tasks.configure_trusted_ca", lambda _bundle: None)

    connector = _connector(url)

    assert connector._proxy_url is None
    assert connector._child_environment() is None


# --- Egress filters and logging -----------------------------------------------


def _helper_filter_rules() -> list[str]:
    script = HOME_EGRESS_HELPER.read_text(encoding="utf-8")
    match = re.search(r"cat >\"\$filter_file\" <<'EOF'\n(.*?)\nEOF\n", script, re.DOTALL)
    assert match is not None
    return match.group(1).splitlines()


def _server_filter_rules() -> list[str]:
    return (EGRESS_DIR / "tinyproxy.filter").read_text(encoding="utf-8").splitlines()


def test_server_and_home_filters_are_the_same_eight_rules():
    assert _server_filter_rules() == EXPECTED_FILTER_RULES
    assert _helper_filter_rules() == EXPECTED_FILTER_RULES


def _allowed(host: str) -> bool:
    # tinyproxy matches the extended regexes case-insensitively against the
    # CONNECT host (FilterCaseSensitive No, FilterURLs No).
    return any(re.search(rule, host, re.IGNORECASE) for rule in EXPECTED_FILTER_RULES)


@pytest.mark.parametrize(
    "host",
    (
        "www.youtube.com",
        "rr1---sn-abc.googlevideo.com",
        "www.bilibili.com",
        "bilibili.com",
        "api.bilibili.com",
        "b23.tv",
        "aisubtitle.hdslb.com",
        "i0.hdslb.com",
        "upos-sz-mirrorcos.bilivideo.com",
        "upos-hz-mirrorakam.akamaized.net",
        "cn-gdsz-cm-01-01.bilivideo.cn",
        "UPOS-HZ-MIRRORAKAM.AKAMAIZED.NET",
    ),
)
def test_filter_allows_the_measured_video_hosts(host):
    assert _allowed(host)


@pytest.mark.parametrize(
    "host",
    (
        "evil-bilibili.com",
        "bilibili.com.evil.net",
        "example.akamaized.net",
        "akamaized.net",
        "upos-evil.akamaized.net.example.com",
        "xupos-hz-mirrorakam.akamaized.net",
        "notbilibili.com",
        "evil-hdslb.com",
        "example.com",
    ),
)
def test_filter_rejects_look_alike_and_unrelated_hosts(host):
    assert not _allowed(host)


def test_both_egress_configs_log_filter_refusals_at_notice():
    example = (EGRESS_DIR / "tinyproxy.conf.example").read_text(encoding="utf-8")
    helper = HOME_EGRESS_HELPER.read_text(encoding="utf-8")

    assert re.search(r"^LogLevel Notice$", example, re.MULTILINE)
    assert re.search(r"^LogLevel Notice$", helper, re.MULTILINE)
    assert "LogLevel Warning" not in example
    assert "LogLevel Warning" not in helper


def test_egress_stays_loopback_connect_443_and_default_deny():
    example = (EGRESS_DIR / "tinyproxy.conf.example").read_text(encoding="utf-8")
    helper = HOME_EGRESS_HELPER.read_text(encoding="utf-8")

    for text in (example, helper):
        assert "Listen 127.0.0.1" in text
        assert "Allow 127.0.0.1" in text
        assert "ConnectPort 443" in text
        assert "FilterDefaultDeny Yes" in text
        assert "0.0.0.0" not in text
    assert "FilterExtended Yes" in example
    assert "FilterType ere" in helper


def test_example_config_names_the_new_setting_only():
    example = (EGRESS_DIR / "tinyproxy.conf.example").read_text(encoding="utf-8")

    assert "US_VIDEO_PROXY_URL" in example
    assert "YOUTUBE_PROXY_URL" not in example
