import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from urllib.error import URLError

from app.connectors.base import NeedsASR, TransientFetchError
from app.connectors.bounded_fetch import (
    EXIT_FETCH_FAILED,
    EXIT_PROXY_AUTHENTICATION_FAILED,
    EXIT_PROXY_TIMEOUT,
    EXIT_PROXY_UNAVAILABLE,
    _proxy_url_error_exit_code,
    fetch_bounded,
)
from app.connectors.youtube import YouTubeConnector, parse_json3
from app.ingest.validate import IngestLimitExceeded


ROOT = Path(__file__).resolve().parents[1]
HOME_EGRESS_HELPER = ROOT / "scripts" / "youtube-home-egress"
RESIDENTIAL_EGRESS_DIR = ROOT / "deploy" / "youtube-egress"
RESIDENTIAL_EGRESS_UNIT = (
    ROOT / "deploy" / "systemd" / "notebook-agent-youtube-egress.service"
)


def test_parse_json3_normalizes_timestamps_and_text():
    body = json.dumps({"events": [{"tStartMs": 1500, "dDurationMs": 2000, "segs": [{"utf8": "hello\n"}, {"utf8": "world"}]}]}).encode()
    cues = parse_json3(body)
    assert (cues[0].start, cues[0].end, cues[0].text) == (1.5, 3.5, "hello world")


def test_url_matching():
    connector = YouTubeConnector()
    assert connector.match("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert connector.match("https://example.com") is None


def test_429_is_transient_and_item_scoped():
    class Result:
        returncode = 1
        stdout = ""
        stderr = "HTTP Error 429: Too Many Requests"

    connector = YouTubeConnector(runner=lambda *a, **k: Result())
    with pytest.raises(TransientFetchError, match="^youtube_rate_limited$"):
        connector.fetch_meta("dQw4w9WgXcQ")


def test_yt_dlp_failure_does_not_expose_raw_stderr_or_retry_without_proxy():
    calls = []

    class Result:
        returncode = 1
        stdout = ""
        stderr = (
            "unable to connect to proxy http://127.0.0.1:18080 while fetching "
            "https://www.youtube.com/watch?private-signed-query"
        )

    connector = YouTubeConnector(
        runner=lambda *args, **kwargs: calls.append((args, kwargs)) or Result(),
        proxy_url="http://127.0.0.1:18080",
    )

    with pytest.raises(TransientFetchError) as caught:
        connector.fetch_meta("dQw4w9WgXcQ")

    assert str(caught.value) == "youtube_proxy_unavailable"
    assert len(calls) == 1
    assert calls[0][1]["env"]["HTTPS_PROXY"] == "http://127.0.0.1:18080"
    assert "private-signed-query" not in str(caught.value)


def test_proxy_timeout_has_stable_classification_and_no_direct_retry():
    calls = []

    def timed_out(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    connector = YouTubeConnector(
        runner=timed_out,
        proxy_url="http://127.0.0.1:18080",
        fetch_timeout_seconds=0.25,
    )

    with pytest.raises(TransientFetchError, match="^youtube_proxy_timeout$"):
        connector.fetch_meta("dQw4w9WgXcQ")

    assert len(calls) == 1


def test_yt_dlp_uses_verified_android_vr_client():
    calls = []

    class Result:
        returncode = 0
        stdout = "{}"
        stderr = ""

    connector = YouTubeConnector(runner=lambda args, **kwargs: calls.append(args) or Result())
    connector.fetch_meta("dQw4w9WgXcQ")
    assert "--extractor-args" in calls[0]
    assert "youtube:player_client=android_vr" in calls[0]


def test_select_track_prefers_original_english_over_chinese_translation():
    connector = YouTubeConnector()
    data = {
        "language": "en-US",
        "subtitles": {},
        "automatic_captions": {
            "zh-Hans": [{}],
            "en-orig": [{}],
        },
    }

    assert connector._select_track(data) == ("auto_caption", "en-orig")


def test_select_track_prefers_official_caption_when_both_match_original_language():
    connector = YouTubeConnector()
    data = {
        "language": "en",
        "subtitles": {"en": [{}]},
        "automatic_captions": {"en-orig": [{}]},
    }

    assert connector._select_track(data) == ("official_cc", "en")


def test_select_track_prefers_automatic_original_language_over_official_translation():
    connector = YouTubeConnector()
    data = {
        "language": "en",
        "subtitles": {"zh-Hans": [{}]},
        "automatic_captions": {"en-orig": [{}]},
    }

    assert connector._select_track(data) == ("auto_caption", "en-orig")


def test_select_track_uses_orig_marker_when_metadata_language_is_missing():
    connector = YouTubeConnector()
    data = {
        "subtitles": {},
        "automatic_captions": {
            "zh-Hans": [{}],
            "en": [{}],
            "fr-orig": [{}],
        },
    }

    assert connector._select_track(data) == ("auto_caption", "fr-orig")


def test_select_track_normalises_underscore_language_codes():
    connector = YouTubeConnector()
    data = {
        "language": "pt_BR",
        "subtitles": {"pt-BR": [{}]},
        "automatic_captions": {"zh-Hans": [{}]},
    }

    assert connector._select_track(data) == ("official_cc", "pt-BR")


def test_select_track_falls_back_to_english_before_chinese():
    connector = YouTubeConnector()
    data = {
        "subtitles": {},
        "automatic_captions": {
            "zh-Hans": [{}],
            "en": [{}],
        },
    }

    assert connector._select_track(data) == ("auto_caption", "en")


def test_fetch_text_returns_needs_asr_when_no_caption_tracks_exist():
    connector = YouTubeConnector()
    connector._meta["dQw4w9WgXcQ"] = {
        "subtitles": {},
        "automatic_captions": {},
    }

    assert isinstance(connector.fetch_text("dQw4w9WgXcQ"), NeedsASR)


class _Response:
    def __init__(self, body: bytes, content_length: str | None = None):
        self._body = body
        self._position = 0
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int) -> bytes:
        chunk = self._body[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


def test_bounded_fetch_rejects_content_length_before_reading_body():
    response = _Response(b"x" * 11, content_length="11")

    with pytest.raises(IngestLimitExceeded):
        fetch_bounded(
            "https://www.youtube.com/api/timedtext",
            headers=None,
            max_bytes=10,
            socket_timeout_seconds=1,
            opener=lambda *_args, **_kwargs: response,
        )

    assert response._position == 0


def test_bounded_fetch_stream_stops_after_limit_without_disk_file():
    response = _Response(b"x" * 11)

    with pytest.raises(IngestLimitExceeded):
        fetch_bounded(
            "https://www.youtube.com/api/timedtext",
            headers=None,
            max_bytes=10,
            socket_timeout_seconds=1,
            opener=lambda *_args, **_kwargs: response,
        )

    assert response._position == 11


@pytest.mark.parametrize(
    ("reason", "proxy_enabled", "exit_code"),
    (
        (ConnectionRefusedError(), True, EXIT_PROXY_UNAVAILABLE),
        (TimeoutError(), True, EXIT_PROXY_TIMEOUT),
        (
            "Tunnel connection failed: 407 Proxy Authentication Required",
            True,
            EXIT_PROXY_AUTHENTICATION_FAILED,
        ),
        (ConnectionRefusedError(), False, EXIT_FETCH_FAILED),
    ),
)
def test_bounded_fetch_classifies_proxy_transport_errors_without_error_text(
    reason,
    proxy_enabled,
    exit_code,
):
    assert _proxy_url_error_exit_code(
        URLError(reason),
        proxy_enabled=proxy_enabled,
    ) == exit_code


def test_fetch_text_uses_bounded_child_process_without_ytdlp_file_download():
    body = json.dumps(
        {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hello"}]}]}
    ).encode()
    calls = []

    class Result:
        returncode = 0
        stdout = body
        stderr = b""

    connector = YouTubeConnector(
        subtitle_runner=lambda args, **kwargs: calls.append((args, kwargs)) or Result(),
        fetch_timeout_seconds=7,
    )
    connector._meta["dQw4w9WgXcQ"] = {
        "language": "en",
        "subtitles": {
            "en": [
                {
                    "ext": "json3",
                    "url": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ",
                }
            ]
        },
        "automatic_captions": {},
    }

    result = connector.fetch_text("dQw4w9WgXcQ")

    assert result.cues[0].text == "hello"
    assert calls[0][0][-2:] == ["-m", "app.connectors.bounded_fetch"]
    assert calls[0][1]["timeout"] == 7
    assert b'"max_bytes": 5000000' in calls[0][1]["input"]


def test_proxy_environment_is_identical_for_both_children_and_parent_is_unchanged(
    monkeypatch,
):
    monkeypatch.setenv("SSL_CERT_FILE", "/verified/ca.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/verified/ca.pem")
    monkeypatch.setenv("ALL_PROXY", "socks5://ambient.invalid:1080")
    monkeypatch.setenv("NO_PROXY", ".youtube.com")
    parent_environment = dict(os.environ)
    calls = []
    body = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "hello"}],
                }
            ]
        }
    ).encode()
    metadata = json.dumps(
        {
            "language": "en",
            "subtitles": {
                "en": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext",
                    }
                ]
            },
            "automatic_captions": {},
        }
    )

    def metadata_runner(_args, **kwargs):
        calls.append(("metadata", kwargs))
        return SimpleNamespace(returncode=0, stdout=metadata, stderr="")

    def subtitle_runner(_args, **kwargs):
        calls.append(("subtitle", kwargs))
        return SimpleNamespace(returncode=0, stdout=body, stderr=b"")

    connector = YouTubeConnector(
        runner=metadata_runner,
        subtitle_runner=subtitle_runner,
        proxy_url="http://127.0.0.1:18080",
    )

    connector.fetch_meta("dQw4w9WgXcQ")
    connector.fetch_text("dQw4w9WgXcQ")

    metadata_environment = calls[0][1]["env"]
    subtitle_environment = calls[1][1]["env"]
    expected_proxy_environment = {
        "HTTP_PROXY": "http://127.0.0.1:18080",
        "HTTPS_PROXY": "http://127.0.0.1:18080",
        "http_proxy": "http://127.0.0.1:18080",
        "https_proxy": "http://127.0.0.1:18080",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
        "SSL_CERT_FILE": "/verified/ca.pem",
        "REQUESTS_CA_BUNDLE": "/verified/ca.pem",
    }
    assert {
        name: metadata_environment[name] for name in expected_proxy_environment
    } == expected_proxy_environment
    assert {
        name: subtitle_environment[name] for name in expected_proxy_environment
    } == expected_proxy_environment
    assert "ALL_PROXY" not in metadata_environment
    assert "all_proxy" not in metadata_environment
    assert os.environ == parent_environment


def test_real_bounded_child_fails_closed_when_loopback_proxy_is_absent():
    connector = YouTubeConnector(
        proxy_url="http://127.0.0.1:9",
        fetch_timeout_seconds=2,
    )

    with pytest.raises(TransientFetchError, match="^youtube_proxy_unavailable$"):
        connector._download_subtitle(
            "https://www.youtube.com/api/timedtext",
            {},
        )


@pytest.mark.parametrize(
    ("returncode", "classification"),
    (
        (12, "youtube_rate_limited"),
        (13, "youtube_proxy_unavailable"),
        (14, "youtube_proxy_timeout"),
        (15, "youtube_proxy_authentication_failed"),
    ),
)
def test_bounded_child_safe_failure_codes_remain_distinguishable(
    returncode,
    classification,
):
    connector = YouTubeConnector(
        subtitle_runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=b"",
            stderr=b"private provider response",
        ),
        proxy_url="http://127.0.0.1:18080",
    )

    with pytest.raises(TransientFetchError, match=rf"^{classification}$"):
        connector._download_subtitle(
            "https://www.youtube.com/api/timedtext",
            {},
        )


def test_fetch_text_terminates_timed_out_bounded_child():
    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    connector = YouTubeConnector(subtitle_runner=timed_out, fetch_timeout_seconds=0.1)
    connector._meta["dQw4w9WgXcQ"] = {
        "language": "en",
        "subtitles": {
            "en": [
                {
                    "ext": "json3",
                    "url": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ",
                }
            ]
        },
        "automatic_captions": {},
    }

    with pytest.raises(TransientFetchError, match="subtitle_fetch_timeout"):
        connector.fetch_text("dQw4w9WgXcQ")


def test_ytdlp_metadata_call_has_hard_wall_clock_timeout():
    seen = {}

    def timed_out(args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    connector = YouTubeConnector(runner=timed_out, fetch_timeout_seconds=0.25)

    with pytest.raises(TransientFetchError, match="youtube_fetch_timeout"):
        connector.fetch_meta("dQw4w9WgXcQ")

    assert seen["timeout"] == 0.25


def test_home_egress_helper_is_loopback_only_bounded_and_foreground():
    script = HOME_EGRESS_HELPER.read_text(encoding="utf-8")

    assert "Listen 127.0.0.1" in script
    assert 'Allow 127.0.0.1' in script
    assert '-R "127.0.0.1:$remote_proxy_port:127.0.0.1:$local_proxy_port"' in script
    assert "0.0.0.0" not in script
    assert "ConnectPort 443" in script
    assert "FilterDefaultDeny Yes" in script
    assert "FilterType ere" in script
    assert "youtube\\.com" in script
    assert "googlevideo\\.com" in script
    assert '"$tinyproxy_bin" -d -c "$config_file"' in script
    assert "ExitOnForwardFailure=yes" in script
    assert "ServerAliveInterval=30" in script
    assert "ServerAliveCountMax=3" in script
    assert "trap cleanup EXIT" in script
    assert "LaunchAgent" not in script
    assert "brew services" not in script
    assert "System Preferences" not in script


def test_home_egress_helper_does_not_install_missing_tinyproxy(tmp_path):
    ssh_stub = tmp_path / "ssh"
    ssh_stub.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    ssh_stub.chmod(0o700)

    result = subprocess.run(
        [
            "/bin/sh",
            str(HOME_EGRESS_HELPER),
            "ubuntu@51.79.159.110",
            "18080",
            "18080",
        ],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": str(tmp_path)},
    )

    assert result.returncode == 69
    assert result.stdout == ""
    assert result.stderr == (
        "youtube-home-egress: tinyproxy is required; install it separately with: "
        "brew install tinyproxy\n"
    )


def _config_directives(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_residential_egress_proxy_is_loopback_only_and_youtube_scoped():
    config = (RESIDENTIAL_EGRESS_DIR / "tinyproxy.conf.example").read_text(
        encoding="utf-8"
    )
    directives = _config_directives(config)

    for expected in (
        "Port 18080",
        "Listen 127.0.0.1",
        "Allow 127.0.0.1",
        "ConnectPort 443",
        "FilterURLs No",
        "FilterExtended Yes",
        "FilterDefaultDeny Yes",
        'Filter "/run/credentials/notebook-agent-youtube-egress.service/filter"',
    ):
        assert expected in directives
    assert "0.0.0.0" not in config
    assert not any(
        line.startswith(("BasicAuth", "LogFile", "User ", "Group "))
        for line in directives
    )
    upstreams = [line for line in directives if line.startswith("Upstream ")]
    assert upstreams == [
        "Upstream http PROXY_USERNAME:PROXY_PASSWORD_country-US_session-SESSION01"
        "@core-residential.evomi.com:1000"
    ]


def test_residential_egress_filter_matches_home_egress_hosts():
    filter_rules = (RESIDENTIAL_EGRESS_DIR / "tinyproxy.filter").read_text(
        encoding="utf-8"
    )
    helper = HOME_EGRESS_HELPER.read_text(encoding="utf-8")

    assert filter_rules.splitlines() == [
        r"(^|\.)youtube\.com$",
        r"(^|\.)googlevideo\.com$",
    ]
    for rule in filter_rules.splitlines():
        assert rule in helper


def test_residential_egress_unit_isolates_provider_credentials():
    unit = RESIDENTIAL_EGRESS_UNIT.read_text(encoding="utf-8")

    assert "DynamicUser=yes" in unit
    assert (
        "LoadCredential=tinyproxy.conf:/etc/notebook-agent/youtube-egress/tinyproxy.conf"
        in unit
    )
    assert (
        "LoadCredential=filter:/etc/notebook-agent/youtube-egress/tinyproxy.filter"
        in unit
    )
    assert (
        "ExecStart=/usr/bin/tinyproxy -d -c ${CREDENTIALS_DIRECTORY}/tinyproxy.conf"
        in unit
    )
    assert "EnvironmentFile" not in unit
    assert "notebook-agent.env" not in unit
    assert "NoNewPrivileges=true" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "ProtectSystem=strict" in unit


def test_residential_egress_apparmor_override_allows_only_unit_credentials():
    override = (RESIDENTIAL_EGRESS_DIR / "apparmor-local-tinyproxy").read_text(
        encoding="utf-8"
    )
    unit = RESIDENTIAL_EGRESS_UNIT.read_text(encoding="utf-8")
    config = (RESIDENTIAL_EGRESS_DIR / "tinyproxy.conf.example").read_text(
        encoding="utf-8"
    )

    credentials_dir = f"/run/credentials/{RESIDENTIAL_EGRESS_UNIT.name}"
    credential_names = [
        line.split("=", 1)[1].split(":", 1)[0]
        for line in unit.splitlines()
        if line.startswith("LoadCredential=")
    ]
    rules = _config_directives(override)

    assert credential_names == ["tinyproxy.conf", "filter"]
    assert rules == [
        f"file r {credentials_dir}/{name}," for name in credential_names
    ]
    assert f'Filter "{credentials_dir}/filter"' in _config_directives(config)
    assert "*" not in override
    assert "w" not in "".join(rule.split()[1] for rule in rules)
