"""Tests for the ``_get_cookie_name`` fallback logging."""

from types import SimpleNamespace
from typing import Never

import pytest
import structlog.testing
from typeguard import suppress_type_checks

from synthorg.api.auth.middleware import _DEFAULT_COOKIE_NAME, _get_cookie_name


@pytest.mark.unit
class TestGetCookieNameFallback:
    """``AttributeError`` / ``TypeError`` logs + falls back to the default."""

    def test_missing_auth_logs_warning(self) -> None:
        class _RaisesOnCookieName:
            @property
            def config(self) -> Never:
                msg = "config missing"
                raise AttributeError(msg)

        app_state = _RaisesOnCookieName()

        with structlog.testing.capture_logs() as logs, suppress_type_checks():
            name = _get_cookie_name(app_state)  # type: ignore[arg-type]

        assert name == _DEFAULT_COOKIE_NAME
        fallback_logs = [
            log for log in logs if log.get("event") == "api.auth.cookie_name_fallback"
        ]
        assert len(fallback_logs) == 1
        log = fallback_logs[0]
        assert log["log_level"] == "warning"
        assert log["error_type"] == "AttributeError"
        assert isinstance(log["error"], str)
        assert log["error"]

    def test_happy_path_returns_configured_cookie_name(self) -> None:
        """Well-formed config returns the configured cookie name (no warning)."""
        auth = SimpleNamespace(cookie_name="my-session")
        api = SimpleNamespace(auth=auth)
        config = SimpleNamespace(api=api)
        app_state = SimpleNamespace(config=config)

        with structlog.testing.capture_logs() as logs, suppress_type_checks():
            name = _get_cookie_name(app_state)  # type: ignore[arg-type]

        assert name == "my-session"
        assert not any(
            log.get("event") == "api.auth.cookie_name_fallback" for log in logs
        )
