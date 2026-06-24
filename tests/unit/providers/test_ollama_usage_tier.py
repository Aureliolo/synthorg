"""Tests for ollama usage-tier resolution (scrape parse + approximation)."""

import pytest

from synthorg.providers.ollama_usage_tier import (
    approximate_tier_from_params,
    parse_usage_tier,
    resolve_usage_tiers,
)


@pytest.mark.unit
class TestParseUsageTier:
    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("<div>Usage</div><div>extra high</div>", 4),
            ("<dt>Usage</dt><dd>extra heavy</dd>", 4),
            ("Usage: high", 3),
            ("<span>Usage:</span> <span>medium</span>", 2),
            ("Usage low", 1),
            ("<li>Usage</li><li>light</li>", 1),
        ],
    )
    def test_parses_label_to_tier(self, html: str, expected: int) -> None:
        assert parse_usage_tier(html) == expected

    def test_returns_none_without_usage_label(self) -> None:
        assert parse_usage_tier("<div>Context: 1M tokens</div>") is None

    def test_returns_none_on_unrecognised_label(self) -> None:
        assert parse_usage_tier("Usage: gargantuan") is None


@pytest.mark.unit
class TestApproximateTier:
    @pytest.mark.parametrize(
        ("parameter_count", "expected"),
        [
            (21_000_000_000, 1),
            (32_000_000_000, 1),
            (33_000_000_000, 2),
            (120_000_000_000, 2),
            (357_000_000_000, 3),
            (600_000_000_000, 3),
            (1_600_000_000_000, 4),
            (None, None),
        ],
    )
    def test_buckets_params_into_tiers(
        self, parameter_count: int | None, expected: int | None
    ) -> None:
        assert approximate_tier_from_params(parameter_count) == expected


@pytest.mark.unit
class TestResolveUsageTiers:
    """``resolve_usage_tiers`` with ``host=None`` returns the approximation."""

    async def test_host_none_approximates_without_scraping(self) -> None:
        params = {
            "small:8b": 8_000_000_000,
            "huge:1t": 1_600_000_000_000,
            "unknown": None,
        }
        result = await resolve_usage_tiers(params, host=None)
        assert result == {"small:8b": 1, "huge:1t": 4, "unknown": None}

    async def test_empty_mapping_returns_empty(self) -> None:
        assert await resolve_usage_tiers({}, host=None) == {}
