"""Tests for the live charter-config resolution used by charter wiring."""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.charter_wiring import (
    _charter_config_provider,
    _resolve_live_charter_config,
)
from synthorg.meta.charter.config import CharterConfig
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _resolver() -> ConfigResolver:
    async def _get_str(namespace: str, key: str) -> str:
        return {
            "interview_model": "example-medium-001",
            "default_currency": "EUR",
        }[key]

    async def _get_int(namespace: str, key: str) -> int:
        return {"interview_max_turns": 5, "interview_max_tokens": 1500}[key]

    async def _get_float(namespace: str, key: str) -> float:
        return 0.9

    return cast(
        "ConfigResolver",
        mock_of[ConfigResolver](
            get_str=AsyncMock(side_effect=_get_str),
            get_int=AsyncMock(side_effect=_get_int),
            get_float=AsyncMock(side_effect=_get_float),
        ),
    )


class TestResolveLiveCharterConfig:
    async def test_reads_all_five_keys_from_resolver(self) -> None:
        live = await _resolve_live_charter_config(_resolver(), fallback=CharterConfig())
        assert live.interview_model == "example-medium-001"
        assert live.default_currency == "EUR"
        assert live.interview_max_turns == 5
        assert live.interview_max_tokens == 1500
        assert live.interview_temperature == pytest.approx(0.9)

    async def test_strategy_discriminator_taken_from_fallback(self) -> None:
        live = await _resolve_live_charter_config(_resolver(), fallback=CharterConfig())
        assert live.interview_strategy == "llm"


class TestCharterConfigProvider:
    async def test_unwired_resolver_yields_boot_fallback(self) -> None:
        state = make_app_state()  # no config resolver wired
        fallback = CharterConfig(interview_max_turns=9)
        provide = _charter_config_provider(state, fallback=fallback)
        assert await provide() == fallback

    async def test_wired_resolver_yields_live_config(self) -> None:
        state = make_app_state(config_resolver=_resolver())
        provide = _charter_config_provider(state, fallback=CharterConfig())
        live = await provide()
        assert live.interview_model == "example-medium-001"
        assert live.interview_max_turns == 5
