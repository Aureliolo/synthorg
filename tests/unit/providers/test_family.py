"""Tests for provider family lookup."""

from unittest.mock import MagicMock

import pytest

from synthorg.providers.family import get_family


def _mock_config(family: str | None = None) -> MagicMock:
    config = MagicMock()
    config.family = family
    return config


@pytest.mark.unit
def test_get_family_returns_explicit_family() -> None:
    configs = {"prov-a": _mock_config(family="family-a")}
    assert get_family("prov-a", configs) == "family-a"


@pytest.mark.unit
def test_get_family_falls_back_to_provider_name() -> None:
    """An unfamilied provider is its own family, so it collides only with itself."""
    configs = {"prov-a": _mock_config(family=None)}
    assert get_family("prov-a", configs) == "prov-a"


@pytest.mark.unit
def test_get_family_unknown_provider_returns_name() -> None:
    assert get_family("unknown", {}) == "unknown"
