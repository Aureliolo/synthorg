"""Tests for quota-aware model selection.

Verifies the full flow: QuotaTracker -> peek_quota_available ->
QuotaAwareSelector -> ModelResolver.
"""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from synthorg.budget.quota import QuotaLimit, QuotaWindow, SubscriptionConfig
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.providers.routing.selector import QuotaAwareSelector

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 15, 14, 30, 0, tzinfo=UTC)


@contextmanager
def _patched_tracker_datetime() -> Generator[MagicMock]:
    with patch("synthorg.budget.quota_tracker.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        yield mock_dt


def _two_provider_config() -> dict[str, ProviderConfig]:
    """Two providers: expensive and cheap, same model, different costs."""
    return {
        "provider-expensive": ProviderConfig(
            driver="litellm",
            connection_name="conn-expensive",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.010,
                    cost_per_1k_output=0.050,
                ),
            ),
        ),
        "provider-cheap": ProviderConfig(
            driver="litellm",
            connection_name="conn-cheap",
            models=(
                ProviderModelConfig(
                    id="test-shared-001",
                    alias="shared",
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.005,
                ),
            ),
        ),
    }


class TestQuotaAwareSelectionIntegration:
    async def test_quota_snapshot_drives_selection(self) -> None:
        """Resolver selects provider-expensive when cheap is exhausted."""
        sub_expensive = SubscriptionConfig(
            quotas=(QuotaLimit(window=QuotaWindow.PER_HOUR, max_requests=100),),
        )
        sub_cheap = SubscriptionConfig(
            quotas=(QuotaLimit(window=QuotaWindow.PER_HOUR, max_requests=2),),
        )
        with _patched_tracker_datetime():
            tracker = QuotaTracker(
                subscriptions={
                    "provider-expensive": sub_expensive,
                    "provider-cheap": sub_cheap,
                },
            )
            await tracker.record_usage("provider-cheap", requests=2)
            snapshot = tracker.peek_quota_available()

        assert snapshot["provider-cheap"] is False
        assert snapshot["provider-expensive"] is True

        selector = QuotaAwareSelector(provider_quota_available=snapshot)
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=selector,
        )
        model = resolver.resolve("shared")
        assert model.provider_name == "provider-expensive"

    async def test_all_exhausted_picks_cheapest(self) -> None:
        """When all providers exhausted, selector falls back to cheapest."""
        sub_expensive = SubscriptionConfig(
            quotas=(QuotaLimit(window=QuotaWindow.PER_HOUR, max_requests=1),),
        )
        sub_cheap = SubscriptionConfig(
            quotas=(QuotaLimit(window=QuotaWindow.PER_HOUR, max_requests=1),),
        )
        with _patched_tracker_datetime():
            tracker = QuotaTracker(
                subscriptions={
                    "provider-expensive": sub_expensive,
                    "provider-cheap": sub_cheap,
                },
            )
            await tracker.record_usage("provider-expensive", requests=1)
            await tracker.record_usage("provider-cheap", requests=1)
            snapshot = tracker.peek_quota_available()

        assert snapshot["provider-expensive"] is False
        assert snapshot["provider-cheap"] is False

        selector = QuotaAwareSelector(provider_quota_available=snapshot)
        resolver = ModelResolver.from_config(
            _two_provider_config(),
            selector=selector,
        )
        model = resolver.resolve("shared")
        assert model.provider_name == "provider-cheap"

    async def test_no_quota_config_defaults_to_cheapest(self) -> None:
        """Without quota tracking, resolver picks cheapest provider."""
        resolver = ModelResolver.from_config(_two_provider_config())
        model = resolver.resolve("shared")
        assert model.provider_name == "provider-cheap"
