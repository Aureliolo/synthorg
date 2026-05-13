"""Tests for the destructive-empty-discovery guard.

The provider sync flow used to silently wipe every persisted model
when discovery returned an empty set (404 / wrong URL / network
blip) paired with ``replace_existing=True``.  The guard now refuses
the destructive path; this file covers the three branches.
"""

from datetime import UTC, datetime

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management._capabilities_mixin import (
    _reject_destructive_empty_discovery,
)
from synthorg.providers.management.capability_dtos import SyncModelsRequest

pytestmark = pytest.mark.unit


_ALIAS_CYCLE = ("small", "medium", "large")


def _config_with_models(count: int) -> ProviderConfig:
    return ProviderConfig(
        driver="litellm",
        auth_type=AuthType.API_KEY,
        api_key="test-key",
        base_url="http://example/api",
        models=tuple(
            # Alias must be unique across the model tuple; cycle
            # through the standard small/medium/large rotation so
            # any model count up to 3 is valid. Tests that need >3
            # only use ``count=29`` for the "wipe-protection" path,
            # which the loop handles by repeating None aliases.
            ProviderModelConfig(
                id=f"test-model-{i:03d}",
                alias=_ALIAS_CYCLE[i] if i < len(_ALIAS_CYCLE) else None,
            )
            for i in range(count)
        ),
        retry=RetryConfig(max_retries=0),
        rate_limiter=RateLimiterConfig(),
        tos_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestRejectDestructiveEmptyDiscovery:
    def test_raises_when_replace_and_empty_and_models_exist(self) -> None:
        with pytest.raises(ProviderValidationError, match="refusing destructive"):
            _reject_destructive_empty_discovery(
                name="ollama-cloud",
                request=SyncModelsRequest(replace_existing=True),
                discovered=(),
                pre_discover=_config_with_models(29),
            )

    def test_allows_when_replace_false_even_with_empty_discovery(self) -> None:
        # Append-only mode is safe even if discovery returns nothing:
        # it adds zero models and removes zero, so the persisted list
        # is preserved verbatim.
        _reject_destructive_empty_discovery(
            name="ollama-cloud",
            request=SyncModelsRequest(replace_existing=False),
            discovered=(),
            pre_discover=_config_with_models(29),
        )

    def test_allows_when_no_existing_models_to_lose(self) -> None:
        # Replace-mode with empty discovery against a provider that
        # never had models is a no-op, not a wipe; let it through.
        _reject_destructive_empty_discovery(
            name="ollama-cloud",
            request=SyncModelsRequest(replace_existing=True),
            discovered=(),
            pre_discover=_config_with_models(0),
        )

    def test_allows_when_discovery_returned_models(self) -> None:
        # The "would wipe" condition only triggers on empty discovery.
        # A non-empty discovery in replace-mode is the happy path.
        discovered = (ProviderModelConfig(id="test-model-001", alias="medium"),)
        _reject_destructive_empty_discovery(
            name="ollama-cloud",
            request=SyncModelsRequest(replace_existing=True),
            discovered=discovered,
            pre_discover=_config_with_models(29),
        )
