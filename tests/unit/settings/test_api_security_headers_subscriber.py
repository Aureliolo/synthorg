"""Tests for ``ApiSecurityHeadersSettingsSubscriber``.

A change to ``api.csp_docs_external_origins`` or ``api.error_docs_base_url``
re-invokes the idempotent ``resolve_runtime_security_settings`` startup step,
which rewrites the module globals the middleware + error taxonomy read per
response, so the change applies within one in-flight response. Tests assert the
re-resolve fires for either key, no-ops on an unexpected pair, and re-raises a
resolve failure.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.lifecycle_helpers import startup_steps
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.api_security_headers_subscriber import (
    ApiSecurityHeadersSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[ApiSecurityHeadersSettingsSubscriber, AppState]:
    app_state = make_app_state(config=RootConfig(company_name="test"))
    sub = ApiSecurityHeadersSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset(
            {
                ("api", "csp_docs_external_origins"),
                ("api", "error_docs_base_url"),
            }
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "api-security-headers"


class TestReapply:
    @pytest.mark.parametrize(
        "key", ["csp_docs_external_origins", "error_docs_base_url"]
    )
    async def test_watched_change_reresolves(
        self, monkeypatch: pytest.MonkeyPatch, key: str
    ) -> None:
        spy = create_autospec(startup_steps.resolve_runtime_security_settings)
        monkeypatch.setattr(startup_steps, "resolve_runtime_security_settings", spy)
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed("api", key)
        spy.assert_awaited_once_with(app_state)

    async def test_unknown_key_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = create_autospec(startup_steps.resolve_runtime_security_settings)
        monkeypatch.setattr(startup_steps, "resolve_runtime_security_settings", spy)
        sub, _ = _make_subscriber()
        await sub.on_settings_changed("api", "unrelated")
        spy.assert_not_awaited()

    async def test_resolve_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(startup_steps.resolve_runtime_security_settings)
        spy.side_effect = RuntimeError("resolve boom")
        monkeypatch.setattr(startup_steps, "resolve_runtime_security_settings", spy)
        sub, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="resolve boom"):
            await sub.on_settings_changed("api", "error_docs_base_url")
