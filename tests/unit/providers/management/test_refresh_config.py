"""Tests for the model-refresh config discriminator and settings loader."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.providers.management.refresh_config import (
    REFRESH_MODE_VALUES,
    ModelRefreshConfig,
    RefreshMode,
    load_model_refresh_config,
    resolve_refresh_mode,
)
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestModelRefreshConfig:
    def test_defaults_are_off_safe(self) -> None:
        cfg = ModelRefreshConfig()
        assert cfg.mode is RefreshMode.OFF
        assert cfg.interval_seconds == 86_400.0
        assert cfg.auto_apply_within_family is False

    def test_frozen(self) -> None:
        cfg = ModelRefreshConfig()
        with pytest.raises(ValidationError):
            cfg.mode = RefreshMode.DETECT_ONLY  # type: ignore[misc]

    def test_interval_floor_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ModelRefreshConfig(interval_seconds=59.0)

    def test_interval_ceiling_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ModelRefreshConfig(interval_seconds=604_801.0)

    def test_refresh_mode_values_match_enum(self) -> None:
        assert REFRESH_MODE_VALUES == (
            "off",
            "manual_only",
            "detect_only",
            "reconcile_recommend",
        )


class TestResolveRefreshMode:
    async def test_reads_live_mode(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="detect_only")
        )
        assert await resolve_refresh_mode(resolver) is RefreshMode.DETECT_ONLY
        resolver.get_str.assert_awaited_once_with("providers", "model_refresh_mode")

    async def test_unknown_value_fails_safe_to_off(self) -> None:
        resolver = mock_of[ConfigResolver](get_str=AsyncMock(return_value="bogus"))
        assert await resolve_refresh_mode(resolver) is RefreshMode.OFF

    async def test_resolver_outage_fails_safe_to_off(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(side_effect=RuntimeError("transient")),
        )
        assert await resolve_refresh_mode(resolver) is RefreshMode.OFF


class TestLoadModelRefreshConfig:
    async def test_assembles_from_settings(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="reconcile_recommend"),
            get_float=AsyncMock(return_value=3600.0),
            get_bool=AsyncMock(return_value=True),
        )
        cfg = await load_model_refresh_config(resolver)
        assert cfg.mode is RefreshMode.RECONCILE_RECOMMEND
        assert cfg.interval_seconds == 3600.0
        assert cfg.auto_apply_within_family is True
