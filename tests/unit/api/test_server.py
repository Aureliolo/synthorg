"""Tests for the Uvicorn server runner."""

import signal
from collections.abc import Callable
from typing import NamedTuple
from unittest.mock import MagicMock, patch

import pytest

from synthorg.api.config import ApiConfig, ServerConfig
from synthorg.config.schema import RootConfig

pytestmark = pytest.mark.unit


class _UvicornRun(NamedTuple):
    """What a ``run_server`` call told uvicorn, and what it chained to."""

    entry: MagicMock
    """The recorder for however uvicorn was entered: ``uvicorn.Config`` on
    the single-process path, ``uvicorn.run`` on a supervised one. Both take
    the app first and the same keywords, so one recorder covers both."""

    server: MagicMock
    """The ``uvicorn.Server`` class mock; ``.return_value`` is the instance."""

    chains: list[Callable[[signal.Signals], None] | None]
    """Every value handed to ``set_shutdown_chain``, in order."""


def _run_recording_uvicorn(server: ServerConfig | None = None) -> _UvicornRun:
    """Run ``run_server`` against a fully stubbed uvicorn.

    Returns:
        The recorded entry call, server mock and shutdown-chain registrations.
    """
    api_config = ApiConfig(server=server) if server is not None else ApiConfig()
    config = RootConfig(
        company_name="test-co",
        api=api_config,
    )
    dummy_app = MagicMock()
    mock_run = MagicMock()
    mock_config = MagicMock()
    mock_server = MagicMock()
    chains: list[Callable[[signal.Signals], None] | None] = []
    with (
        patch(
            "synthorg.api.server.create_app",
            return_value=dummy_app,
        ),
        patch("synthorg.api.server.uvicorn.run", mock_run),
        patch("synthorg.api.server.uvicorn.Config", mock_config),
        patch("synthorg.api.server.uvicorn.Server", mock_server),
        patch("synthorg.api.server.set_shutdown_chain", chains.append),
    ):
        from synthorg.api.server import run_server

        run_server(config)
    return _UvicornRun(
        entry=mock_config if mock_config.called else mock_run,
        server=mock_server,
        chains=chains,
    )


def _run_with_config(
    server: ServerConfig | None = None,
) -> MagicMock:
    """Helper: run_server with uvicorn stubbed and return the entry recorder.

    Returns:
        The mock standing in for however uvicorn was entered.
    """
    return _run_recording_uvicorn(server).entry


class TestRunServerUvicornParams:
    """Verify that run_server passes correct params to uvicorn.run."""

    def test_access_log_disabled_and_log_config_none(self) -> None:
        """Uvicorn access log is disabled; log_config is None."""
        mock_run = _run_with_config()
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["access_log"] is False
        assert call_kwargs.kwargs["log_config"] is None

    def test_no_tls_kwargs_by_default(self) -> None:
        mock_run = _run_with_config()
        kw = mock_run.call_args.kwargs
        assert "ssl_certfile" not in kw
        assert "ssl_keyfile" not in kw
        assert "ssl_ca_certs" not in kw

    def test_tls_kwargs_passed_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_SSL_CERTFILE", "/etc/tls/cert.pem")
        monkeypatch.setenv("SYNTHORG_API_SSL_KEYFILE", "/etc/tls/key.pem")
        mock_run = _run_with_config()
        kw = mock_run.call_args.kwargs
        assert kw["ssl_certfile"] == "/etc/tls/cert.pem"
        assert kw["ssl_keyfile"] == "/etc/tls/key.pem"
        assert "ssl_ca_certs" not in kw

    def test_tls_with_ca_certs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_API_SSL_CERTFILE", "/etc/tls/cert.pem")
        monkeypatch.setenv("SYNTHORG_API_SSL_KEYFILE", "/etc/tls/key.pem")
        monkeypatch.setenv("SYNTHORG_API_SSL_CA_CERTS", "/etc/tls/ca.pem")
        mock_run = _run_with_config()
        kw = mock_run.call_args.kwargs
        assert kw["ssl_ca_certs"] == "/etc/tls/ca.pem"

    def test_no_proxy_headers_by_default(self) -> None:
        mock_run = _run_with_config()
        kw = mock_run.call_args.kwargs
        assert "forwarded_allow_ips" not in kw
        assert "proxy_headers" not in kw

    def test_proxy_headers_when_trusted_proxies_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_TRUSTED_PROXIES",
            '["10.0.0.1", "172.16.0.0/12"]',
        )
        mock_run = _run_with_config()
        kw = mock_run.call_args.kwargs
        assert kw["forwarded_allow_ips"] == "10.0.0.1,172.16.0.0/12"
        assert kw["proxy_headers"] is True


class TestRunServerWorkerTopology:
    """Worker / reload topology must reach uvicorn correctly.

    A pre-built app object makes uvicorn silently drop ``workers`` /
    ``reload``; multi-process topologies must be driven from an import
    string so each subprocess rebuilds the app.
    """

    def test_single_worker_passes_object_not_factory(self) -> None:
        mock_run = _run_with_config(ServerConfig(workers=1, reload=False))
        call = mock_run.call_args
        # First positional arg is the pre-built drain wrapper, and the
        # factory flag is off.
        assert not isinstance(call.args[0], str)
        assert call.kwargs["factory"] is False

    def test_multi_worker_uses_import_string_factory(self) -> None:
        mock_run = _run_with_config(ServerConfig(workers=4))
        call = mock_run.call_args
        assert call.args[0] == "synthorg.api.server:create_drain_app"
        assert call.kwargs["factory"] is True
        assert call.kwargs["workers"] == 4

    def test_reload_uses_import_string_factory(self) -> None:
        mock_run = _run_with_config(ServerConfig(workers=1, reload=True))
        call = mock_run.call_args
        assert call.args[0] == "synthorg.api.server:create_drain_app"
        assert call.kwargs["factory"] is True
        assert call.kwargs["reload"] is True

    def test_reload_with_multiple_workers_rejected(self) -> None:
        with pytest.raises(ValueError, match="reload requires workers == 1"):
            _run_with_config(ServerConfig(workers=2, reload=True))


class TestShutdownChainOwnership:
    """Who stops the process when SIGTERM arrives.

    The app's lifespan startup installs its own signal handlers, and
    ``loop.add_signal_handler`` REPLACES uvicorn's, so the entry point that
    owns the server must hand those handlers somewhere to pass the signal
    on. Without it the process observes SIGTERM, keeps running, and is
    SIGKILLed at the orchestrator's grace deadline having torn nothing down.
    """

    def test_single_process_chains_the_signal_to_the_server(self) -> None:
        run = _run_recording_uvicorn(ServerConfig(workers=1, reload=False))
        chain = run.chains[0]
        assert chain is not None
        chain(signal.SIGTERM)
        run.server.return_value.handle_exit.assert_called_once_with(
            signal.SIGTERM,
            None,
        )

    def test_single_process_clears_the_chain_when_the_server_exits(self) -> None:
        # A second run in the same process must not chain into a server
        # that has already returned from ``run()``.
        run = _run_recording_uvicorn(ServerConfig(workers=1, reload=False))
        assert run.chains[-1] is None

    def test_single_process_runs_the_server_it_chained_to(self) -> None:
        run = _run_recording_uvicorn(ServerConfig(workers=1, reload=False))
        run.server.return_value.run.assert_called_once_with()

    def test_supervised_registers_no_chain(self) -> None:
        # A reloader or worker pool is a supervisor that owns signals and
        # forwards them to children; there is no single server object here
        # to chain to, so the handlers must not be installed at all.
        run = _run_recording_uvicorn(ServerConfig(workers=4))
        assert run.chains == []
        run.server.assert_not_called()
