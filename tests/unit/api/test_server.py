"""Tests for the Uvicorn server runner."""

from unittest.mock import MagicMock, patch

import pytest

from synthorg.api.config import ApiConfig, ServerConfig
from synthorg.config.schema import RootConfig

pytestmark = pytest.mark.unit


def _run_with_config(
    server: ServerConfig | None = None,
) -> MagicMock:
    """Helper: run_server with a mock uvicorn.run and return the mock."""
    api_config = ApiConfig(server=server) if server is not None else ApiConfig()
    config = RootConfig(
        company_name="test-co",
        api=api_config,
    )
    dummy_app = MagicMock()
    mock_run = MagicMock()
    with (
        patch(
            "synthorg.api.server.create_app",
            return_value=dummy_app,
        ),
        patch("synthorg.api.server.uvicorn.run", mock_run),
    ):
        from synthorg.api.server import run_server

        run_server(config)
    return mock_run


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
