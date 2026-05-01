"""Tests for provider management helper functions."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.api.dto import UpdateProviderRequest
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.management._helpers import (
    _apply_credential_updates,
    apply_update,
    build_discovery_headers,
    models_from_litellm,
)


def _make_config(
    *,
    auth_type: AuthType = AuthType.API_KEY,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> ProviderConfig:
    """Build a ProviderConfig with sensible defaults for testing."""
    return ProviderConfig(
        driver="litellm",
        auth_type=auth_type,
        api_key=api_key,
        base_url=base_url,
        models=(
            ProviderModelConfig(
                id="test-model-001",
                alias="medium",
            ),
        ),
        retry=RetryConfig(max_retries=0),
        rate_limiter=RateLimiterConfig(),
        **kwargs,
    )


@pytest.mark.unit
class TestBuildDiscoveryHeaders:
    def test_subscription_returns_bearer(self) -> None:
        """Subscription auth with token returns Authorization Bearer header."""
        config = _make_config(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="test-subscription-token",
            tos_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        headers = build_discovery_headers(config)
        assert headers == {"Authorization": "Bearer test-subscription-token"}

    def test_subscription_no_token_returns_none(self) -> None:
        """Subscription auth without a token returns None."""
        config = _make_config(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="test-subscription-token",
            tos_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        # Bypass frozen model to simulate a cleared token
        object.__setattr__(config, "subscription_token", None)
        headers = build_discovery_headers(config)
        assert headers is None


@pytest.mark.unit
class TestApplyCredentialUpdates:
    def test_switch_from_subscription_to_api_key_clears_fields(self) -> None:
        """Switching from subscription to api_key clears token and tos."""
        updates: dict[str, Any] = {}
        request = UpdateProviderRequest(
            auth_type=AuthType.API_KEY,
            api_key=SecretStr("sk-new"),
        )
        _apply_credential_updates(updates, request, AuthType.API_KEY)
        assert updates["api_key"] == "sk-new"
        assert updates["subscription_token"] is None
        assert updates["tos_accepted_at"] is None

    def test_switch_to_subscription_sets_token(self) -> None:
        """Switching to subscription sets subscription_token when provided."""
        updates: dict[str, Any] = {}
        request = UpdateProviderRequest(
            subscription_token=SecretStr("test-subscription-token"),
        )
        _apply_credential_updates(updates, request, AuthType.SUBSCRIPTION)
        assert updates["subscription_token"] == "test-subscription-token"

    def test_tos_accepted_stamps_timestamp(self) -> None:
        """Setting tos_accepted=True stamps tos_accepted_at."""
        updates: dict[str, Any] = {}
        request = UpdateProviderRequest(tos_accepted=True)
        frozen = datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)
        with patch(
            "synthorg.providers.management._helpers.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = frozen
            mock_dt.side_effect = datetime
            _apply_credential_updates(updates, request, AuthType.SUBSCRIPTION)
        assert updates["tos_accepted_at"] == frozen

    def test_clear_subscription_token(self) -> None:
        """Setting clear_subscription_token=True clears the token."""
        updates: dict[str, Any] = {}
        request = UpdateProviderRequest(clear_subscription_token=True)
        _apply_credential_updates(updates, request, AuthType.SUBSCRIPTION)
        assert updates["subscription_token"] is None


@pytest.mark.unit
class TestApplyUpdateAuthTransitions:
    """Integration-level tests for apply_update subscription transitions."""

    def test_switch_from_subscription_to_api_key_clears_owned_fields(
        self,
    ) -> None:
        """AUTH_OWNED_FIELDS cleanup clears subscription fields on switch."""
        existing = _make_config(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="test-subscription-token",
            tos_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        request = UpdateProviderRequest(
            auth_type=AuthType.API_KEY,
            api_key=SecretStr("sk-new-key"),
        )
        result = apply_update(existing, request)
        assert result.auth_type == AuthType.API_KEY
        assert result.api_key == "sk-new-key"
        assert result.subscription_token is None
        assert result.tos_accepted_at is None

    def test_switch_from_api_key_to_subscription_clears_api_key(
        self,
    ) -> None:
        """Switching to subscription clears api_key and sets token."""
        existing = _make_config(
            auth_type=AuthType.API_KEY,
            api_key="sk-old",
        )
        request = UpdateProviderRequest(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token=SecretStr("test-subscription-token"),
            tos_accepted=True,
        )
        result = apply_update(existing, request)
        assert result.auth_type == AuthType.SUBSCRIPTION
        assert result.api_key is None
        assert result.subscription_token == "test-subscription-token"
        assert result.tos_accepted_at is not None


def _fake_model_cost() -> dict[str, Any]:
    """Build a realistic litellm.model_cost subset for testing."""
    return {
        "test-provider/test-large-001": {
            "litellm_provider": "test-provider",
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "max_input_tokens": 200_000,
        },
        "test-provider/test-large-001-20260205": {
            "litellm_provider": "test-provider",
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "max_input_tokens": 200_000,
        },
        "test-provider/test-small-001": {
            "litellm_provider": "test-provider",
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
            "max_input_tokens": 128_000,
        },
        "other-provider/other-model": {
            "litellm_provider": "other-provider",
            "input_cost_per_token": 0.00001,
            "output_cost_per_token": 0.00003,
            "max_input_tokens": 100_000,
        },
        "not-a-dict-entry": "malformed",
    }


@pytest.mark.unit
class TestModelsFromLitellm:
    """Tests for ``models_from_litellm`` LiteLLM database lookup."""

    @patch("litellm.model_cost", _fake_model_cost())
    def test_returns_matching_models(self) -> None:
        """Returns models filtered to the requested provider."""
        result = models_from_litellm("test-provider")

        assert len(result) == 2
        ids = {m.id for m in result}
        assert "test-large-001" in ids
        assert "test-small-001" in ids
        assert "other-model" not in ids

    @patch("litellm.model_cost", _fake_model_cost())
    def test_deduplicates_dated_variants(self) -> None:
        """Prefers shorter model ID over dated variant."""
        result = models_from_litellm("test-provider")

        large_models = [m for m in result if "large" in m.id]
        assert len(large_models) == 1
        assert large_models[0].id == "test-large-001"

    @patch("litellm.model_cost", _fake_model_cost())
    def test_strips_provider_prefix(self) -> None:
        """Strips provider/ prefix from model IDs."""
        result = models_from_litellm("test-provider")

        for m in result:
            assert not m.id.startswith("test-provider/")

    @patch(
        "litellm.model_cost",
        {
            "test-provider/null-cost-model": {
                "litellm_provider": "test-provider",
                "input_cost_per_token": None,
                "output_cost_per_token": None,
                "max_input_tokens": 50_000,
            },
        },
    )
    def test_none_cost_values_default_to_zero(self) -> None:
        """None cost values in litellm data are treated as zero."""
        result = models_from_litellm("test-provider")

        assert len(result) == 1
        assert result[0].cost_per_1k_input == 0.0
        assert result[0].cost_per_1k_output == 0.0

    @patch(
        "litellm.model_cost",
        {
            "test-provider/string-max-model": {
                "litellm_provider": "test-provider",
                "input_cost_per_token": 0.00001,
                "output_cost_per_token": 0.00005,
                "max_input_tokens": "unlimited",
            },
        },
    )
    def test_non_int_max_input_falls_back_to_default(self) -> None:
        """Non-integer max_input_tokens falls back to default."""
        result = models_from_litellm("test-provider")

        assert len(result) == 1
        assert result[0].max_context == 200_000

    @patch("litellm.model_cost", _fake_model_cost())
    def test_skips_non_dict_entries(self) -> None:
        """Non-dict entries in model_cost are safely skipped."""
        result = models_from_litellm("test-provider")

        # Should still return valid models despite "not-a-dict-entry"
        assert len(result) == 2

    @patch("litellm.model_cost", _fake_model_cost())
    def test_empty_results_for_unknown_provider(self) -> None:
        """Unknown provider returns empty tuple."""
        result = models_from_litellm("nonexistent-provider")

        assert result == ()

    def test_version_filter_applied(self) -> None:
        """Version filter regex excludes non-matching models."""
        import re

        with (
            patch("litellm.model_cost", _fake_model_cost()),
            patch(
                "synthorg.providers.presets.MODEL_VERSION_FILTERS",
                {"test-provider": re.compile(r"^test-large")},
            ),
        ):
            result = models_from_litellm("test-provider")

        assert len(result) == 1
        assert result[0].id == "test-large-001"

    def test_import_failure_returns_empty(self) -> None:
        """Returns empty tuple when litellm is not installed."""
        import builtins
        import sys

        # Temporarily remove litellm from sys.modules to force re-import
        saved = sys.modules.pop("litellm", None)
        original_import = builtins.__import__

        def mock_import(
            name: str,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            if name == "litellm":
                raise ImportError(name)
            return original_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=mock_import):
                result = models_from_litellm("test-provider")
            assert result == ()
        finally:
            if saved is not None:
                sys.modules["litellm"] = saved

    @patch("litellm.model_cost", _fake_model_cost())
    def test_results_sorted_by_id(self) -> None:
        """Results are sorted alphabetically by model ID."""
        result = models_from_litellm("test-provider")

        ids = [m.id for m in result]
        assert ids == sorted(ids)

    @patch("litellm.model_cost", _fake_model_cost())
    def test_populates_cost_fields(self) -> None:
        """Cost fields are correctly converted to per-1k pricing."""
        result = models_from_litellm("test-provider")

        small = next(m for m in result if m.id == "test-small-001")
        assert small.cost_per_1k_input == round(0.000003 * 1000, 6)
        assert small.cost_per_1k_output == round(0.000015 * 1000, 6)
        assert small.max_context == 128_000
