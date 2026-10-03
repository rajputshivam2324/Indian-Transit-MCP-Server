"""Server transport settings, DNS-rebinding settings, and the entrypoint's transport choice."""

from __future__ import annotations

import importlib
import os

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from tests.segment_fixtures import make_container
from transit_mcp.config import Settings
from transit_mcp.server.app import Container
from transit_mcp.server.main import create_app, listen_url, transport_security_for

# `transit_mcp.server` re-exports the `main` *function*, which shadows the `main` submodule
# as a package attribute, so the module has to be fetched by its dotted name.
main_module = importlib.import_module("transit_mcp.server.main")


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Nothing from the developer's shell may change what these tests see."""
    for name in [n for n in os.environ if n.startswith("TRANSIT_")]:
        monkeypatch.delenv(name)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_defaults_serve_streamable_http_on_loopback_only():
    s = _settings()
    assert s.transport == "streamable-http"
    assert (s.server_host, s.server_port, s.server_path) == ("127.0.0.1", 8000, "/mcp")
    assert s.allowed_host_list == [] and s.allowed_origin_list == []


def test_every_server_setting_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRANSIT_TRANSPORT", "stdio")
    monkeypatch.setenv("TRANSIT_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("TRANSIT_SERVER_PORT", "9001")
    monkeypatch.setenv("TRANSIT_SERVER_PATH", "transit/mcp")
    monkeypatch.setenv("TRANSIT_ALLOWED_HOSTS", "a.example, b.example:*")
    monkeypatch.setenv("TRANSIT_ALLOWED_ORIGINS", "https://a.example")
    s = _settings()
    assert (s.transport, s.server_host, s.server_port) == ("stdio", "0.0.0.0", 9001)
    assert s.server_path == "/transit/mcp"
    assert s.allowed_host_list == ["a.example", "b.example:*"]
    assert s.allowed_origin_list == ["https://a.example"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("mcp", "/mcp"), ("/mcp/", "/mcp"), ("", "/"), ("/", "/"), (" /a/b/ ", "/a/b")],
)
def test_server_path_is_normalised(raw, expected):
    assert _settings(server_path=raw).server_path == expected


def test_allowed_lists_split_on_commas_and_drop_blanks():
    s = _settings(allowed_hosts=" a.example , ,b.example,, ", allowed_origins=",")
    assert s.allowed_host_list == ["a.example", "b.example"]
    assert s.allowed_origin_list == []


@pytest.mark.parametrize(
    "bad",
    [{"server_port": 0}, {"server_port": 65536}, {"transport": "sse"}, {"transport": "http"}],
)
def test_invalid_server_settings_are_rejected(bad):
    with pytest.raises(ValidationError):
        _settings(**bad)


# --------------------------------------------------------------------------- #
# DNS-rebinding settings
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_no_allow_lists_keeps_the_sdk_default(host):
    assert transport_security_for(_settings(server_host=host)) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_allowed_hosts_extend_the_loopback_defaults(host):
    ts = transport_security_for(
        _settings(
            server_host=host,
            allowed_hosts="mcp.example.com,mcp.example.com:8443",
            allowed_origins="https://app.example.com",
        )
    )
    assert ts.enable_dns_rebinding_protection is True
    assert ts.allowed_hosts == [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "mcp.example.com",
        "mcp.example.com:8443",
    ]
    assert ts.allowed_origins == [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "https://app.example.com",
    ]


def test_allow_lists_are_the_only_hosts_on_a_non_loopback_bind():
    ts = transport_security_for(_settings(server_host="0.0.0.0", allowed_hosts="mcp.example.com"))
    assert ts.enable_dns_rebinding_protection is True
    assert ts.allowed_hosts == ["mcp.example.com"] and ts.allowed_origins == []


# --------------------------------------------------------------------------- #
# create_app
# --------------------------------------------------------------------------- #
async def test_create_app_hands_the_http_settings_to_fastmcp():
    container = make_container()
    try:
        mcp, _ = create_app(
            _settings(server_host="localhost", server_port=9001, server_path="transit"),
            container,
        )
        assert (mcp.settings.host, mcp.settings.port) == ("localhost", 9001)
        assert mcp.settings.streamable_http_path == "/transit"
        # Loopback: the SDK switched DNS-rebinding protection on because host was a
        # constructor argument.
        assert mcp.settings.transport_security.enable_dns_rebinding_protection is True
    finally:
        await container.aclose()


async def test_create_app_leaves_a_public_bind_unprotected_unless_told_otherwise():
    container = make_container()
    try:
        mcp, _ = create_app(_settings(server_host="0.0.0.0"), container)
        assert mcp.settings.transport_security is None
    finally:
        await container.aclose()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "http://127.0.0.1:8000/mcp"),
        ({"server_host": "::1", "server_port": 9001}, "http://[::1]:9001/mcp"),
        (
            {"server_host": "0.0.0.0", "server_path": "/transit/mcp"},
            "http://0.0.0.0:8000/transit/mcp",
        ),
    ],
)
def test_listen_url(kwargs, expected):
    assert listen_url(_settings(**kwargs)) == expected


# --------------------------------------------------------------------------- #
# main(): transport choice and start-up messages
# --------------------------------------------------------------------------- #
class _Recorder:
    """Stands in for FastMCP.run so nothing actually starts listening."""

    def __init__(self, monkeypatch, *, fail: BaseException | None = None):
        self.transports: list[str] = []
        self.closed = 0
        recorder = self

        def run(self, transport="stdio", mount_path=None):
            recorder.transports.append(transport)
            if fail is not None:
                raise fail

        async def aclose(self):
            recorder.closed += 1

        monkeypatch.setattr(FastMCP, "run", run)
        monkeypatch.setattr(Container, "aclose", aclose)


def test_main_serves_streamable_http_by_default(monkeypatch):
    rec = _Recorder(monkeypatch)
    monkeypatch.setattr(main_module, "get_settings", lambda: _settings())
    main_module.main()
    assert rec.transports == ["streamable-http"]
    assert rec.closed == 1  # the shared upstream client is closed on the way out


def test_main_can_still_serve_stdio(monkeypatch):
    rec = _Recorder(monkeypatch)
    monkeypatch.setattr(main_module, "get_settings", lambda: _settings(transport="stdio"))
    main_module.main()
    assert rec.transports == ["stdio"]


def test_main_closes_the_upstream_client_even_if_the_server_crashes(monkeypatch):
    rec = _Recorder(monkeypatch, fail=RuntimeError("server crashed"))
    monkeypatch.setattr(main_module, "get_settings", lambda: _settings())
    with pytest.raises(RuntimeError, match="server crashed"):
        main_module.main()
    assert rec.closed == 1


def test_ctrl_c_is_a_clean_shutdown_not_a_traceback(monkeypatch):
    rec = _Recorder(monkeypatch, fail=KeyboardInterrupt())
    monkeypatch.setattr(main_module, "get_settings", lambda: _settings())
    main_module.main()  # returns normally instead of re-raising
    assert rec.closed == 1


class _Log:
    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg, *args):
        self.infos.append(msg % args)

    def warning(self, msg, *args):
        self.warnings.append(msg % args)


def _announce(monkeypatch, **kw) -> _Log:
    log = _Log()
    monkeypatch.setattr(main_module, "log", log)
    main_module._announce_http(_settings(**kw))
    return log


def test_loopback_start_up_is_quiet(monkeypatch):
    log = _announce(monkeypatch)
    assert log.infos == ["serving MCP over streamable HTTP at http://127.0.0.1:8000/mcp"]
    assert log.warnings == []


def test_public_bind_warns_about_missing_authentication_and_protection(monkeypatch):
    log = _announce(monkeypatch, server_host="0.0.0.0")
    assert len(log.warnings) == 2
    assert "NO authentication" in log.warnings[0] and "http://0.0.0.0:8000/mcp" in log.warnings[0]
    assert "DNS-rebinding protection is OFF" in log.warnings[1]


def test_public_bind_with_allow_list_only_warns_about_authentication(monkeypatch):
    log = _announce(monkeypatch, server_host="0.0.0.0", allowed_hosts="mcp.example.com")
    assert len(log.warnings) == 1 and "NO authentication" in log.warnings[0]
