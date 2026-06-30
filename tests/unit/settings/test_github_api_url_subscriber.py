"""Tests for GithubApiUrlSettingsSubscriber: re-binds the health-checker URL."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.github_api_url_subscriber import (
    GithubApiUrlSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_BIND_TARGET = "synthorg.integrations.health.prober.bind_github_default_api_url"


def _make_subscriber(
    resolver: ConfigResolver | None = None,
) -> GithubApiUrlSettingsSubscriber:
    state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    return GithubApiUrlSettingsSubscriber(
        app_state=state,
        settings_service=mock_of[SettingsService](),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        assert isinstance(_make_subscriber(), SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        assert _make_subscriber().subscriber_name == "github-api-url"

    def test_watches_only_github_api_url(self) -> None:
        watched = _make_subscriber().watched_keys
        assert watched == frozenset({("integrations", "github_api_url")})


class TestRebind:
    async def test_change_rebinds_resolved_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bind = MagicMock()
        monkeypatch.setattr(_BIND_TARGET, bind)
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="https://github.example.com/api/v3"),
        )
        sub = _make_subscriber(resolver)

        await sub.on_settings_changed("integrations", "github_api_url")

        bind.assert_called_once_with("https://github.example.com/api/v3")

    async def test_resolve_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bind = MagicMock()
        monkeypatch.setattr(_BIND_TARGET, bind)
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(side_effect=RuntimeError("db down")),
        )
        sub = _make_subscriber(resolver)

        with pytest.raises(RuntimeError, match="db down"):
            await sub.on_settings_changed("integrations", "github_api_url")
        bind.assert_not_called()
