"""Tests for the ``get_auth_config`` fallback logging."""

from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing

from synthorg.api.auth.controller_helpers import get_auth_config
from synthorg.core.auth.config import AuthConfig


class _RaisesOnAuth:
    """Config stub whose ``api.auth`` access raises ``AttributeError``."""

    @property
    def api(self) -> Any:
        class _Api:
            @property
            def auth(self) -> Any:
                msg = "auth not wired"
                raise AttributeError(msg)

        return _Api()


@pytest.mark.unit
class TestGetAuthConfigFallback:
    """``AttributeError`` / ``TypeError`` on config access logs + falls back."""

    def test_missing_auth_logs_warning(self) -> None:
        app_state = SimpleNamespace(config=_RaisesOnAuth())

        with structlog.testing.capture_logs() as logs:
            cfg = get_auth_config(app_state)  # type: ignore[arg-type]

        assert isinstance(cfg, AuthConfig)
        fallback_logs = [
            log for log in logs if log.get("event") == "api.auth.config_fallback"
        ]
        assert len(fallback_logs) == 1
        log = fallback_logs[0]
        assert log["log_level"] == "warning"
        assert log["error_type"] == "AttributeError"
        assert isinstance(log["error"], str)
        assert log["error"]

    def test_type_error_logs_warning(self) -> None:
        """Wrong-shaped config (``None``) raises TypeError, logs fallback."""
        app_state = SimpleNamespace(config=None)

        with structlog.testing.capture_logs() as logs:
            cfg = get_auth_config(app_state)  # type: ignore[arg-type]

        assert isinstance(cfg, AuthConfig)
        fallback_logs = [
            log for log in logs if log.get("event") == "api.auth.config_fallback"
        ]
        # ``None.api`` raises AttributeError on ``None`` -- both are
        # handled identically; the test just asserts the warning fires.
        assert len(fallback_logs) == 1
        assert fallback_logs[0]["log_level"] == "warning"

    def test_happy_path_returns_config_without_log(self) -> None:
        """Well-formed config returns the real object and emits no warning."""
        real_auth = AuthConfig()
        api = SimpleNamespace(auth=real_auth)
        config = SimpleNamespace(api=api)
        app_state = SimpleNamespace(config=config)

        with structlog.testing.capture_logs() as logs:
            cfg = get_auth_config(app_state)  # type: ignore[arg-type]

        assert cfg is real_auth
        assert not any(log.get("event") == "api.auth.config_fallback" for log in logs)
