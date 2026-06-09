"""Tests for provider management helper functions."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from synthorg.api.dto_providers import UpdateProviderRequest
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.management._helpers import (
    _apply_credential_updates,
    _coerce_cost,
    apply_update,
    build_discovery_headers,
    models_from_litellm,
)


def _make_config(
    *,
    auth_type: AuthType = AuthType.API_KEY,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: object,
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
        **kwargs,  # type: ignore[arg-type]
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
        updates: dict[str, object] = {}
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
        updates: dict[str, object] = {}
        request = UpdateProviderRequest(
            subscription_token=SecretStr("test-subscription-token"),
        )
        _apply_credential_updates(updates, request, AuthType.SUBSCRIPTION)
        assert updates["subscription_token"] == "test-subscription-token"

    def test_tos_accepted_stamps_timestamp(self) -> None:
        """Setting tos_accepted=True stamps tos_accepted_at."""
        updates: dict[str, object] = {}
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
        updates: dict[str, object] = {}
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

    def test_non_auth_type_value_logs_and_rejects(self) -> None:
        """A non-``AuthType`` ``auth_type`` is logged and rejected loudly.

        ``UpdateProviderRequest`` validation normally rejects an invalid
        ``auth_type``; ``model_construct`` bypasses it to reach
        ``apply_update``'s defensive ``isinstance`` guard. The guard logs
        before falling back to the existing auth type, and the bad value
        is then rejected by the final ``ProviderConfig`` validation rather
        than silently applied.
        """
        existing = _make_config(auth_type=AuthType.API_KEY, api_key="sk-old")
        request = UpdateProviderRequest.model_construct(
            auth_type="bogus",  # type: ignore[arg-type]  # deliberately invalid
            api_key=None,
            clear_api_key=False,
            subscription_token=None,
            clear_subscription_token=False,
            tos_accepted=False,
        )
        with (
            patch("synthorg.providers.management._helpers.logger") as mock_logger,
            pytest.raises(ValidationError),
        ):
            apply_update(existing, request)
        mock_logger.warning.assert_called_once()
        assert (
            mock_logger.warning.call_args.args[0]
            == "provider.management.update_auth_type_unexpected"
        )


@pytest.mark.unit
class TestCoerceCost:
    """Direct tests for the ``_coerce_cost`` numeric guard."""

    @pytest.mark.parametrize("value", [0.000015, 0, 1, 0.0])
    def test_accepts_real_numbers(self, value: float) -> None:
        assert _coerce_cost(value) == float(value)

    @pytest.mark.parametrize("value", [True, False])
    def test_rejects_bool(self, value: bool) -> None:
        """``bool`` is an ``int`` subtype; reject it so ``True`` is not 1.0."""
        with pytest.raises(TypeError):
            _coerce_cost(value)

    @pytest.mark.parametrize("value", ["0.001", None, [1, 2], {"a": 1}])
    def test_rejects_non_numeric(self, value: object) -> None:
        """Strings and containers are rejected (caller skips the entry)."""
        with pytest.raises(TypeError):
            _coerce_cost(value)  # type: ignore[arg-type]


def _fake_model_cost() -> dict[str, object]:
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
            *args: object,
            **kwargs: object,
        ) -> object:
            if name == "litellm":
                raise ImportError(name)
            return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

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


@pytest.mark.unit
class TestDiffProviderUpdate:
    """Tests for the structured field-diff audit payload.

    The EDIT form on the frontend re-sends every field on every
    submit, so ``request.model_dump(exclude_unset=True)`` would mark
    every field as "changed" even when the user only touched
    ``base_url``.  Comparing the persisted ``existing`` config
    against the post-merge ``updated`` config produces the
    operator-meaningful diff; sensitive fields must collapse to
    ``"<redacted>"`` so credentials never reach the audit table.
    """

    def test_only_changed_fields_appear(self) -> None:
        from synthorg.providers.management.service import _diff_provider_update

        before = _make_config(base_url="http://old.example/api")
        after = before.model_copy(update={"base_url": "http://new.example/api"})

        diff = _diff_provider_update(before, after)

        assert diff["fields_changed"] == ["base_url"]
        inner = diff["diff"]
        assert isinstance(inner, dict)
        assert inner == {
            "base_url": {
                "old": "http://old.example/api",
                "new": "http://new.example/api",
            },
        }

    def test_sensitive_fields_collapse_to_redacted_sentinel(self) -> None:
        from synthorg.providers.management.service import _diff_provider_update

        before = _make_config(api_key="old-secret-do-not-leak")
        after = before.model_copy(update={"api_key": "new-secret-do-not-leak"})

        diff = _diff_provider_update(before, after)
        assert diff["fields_changed"] == ["api_key"]
        inner = diff["diff"]
        assert isinstance(inner, dict)
        # Neither the prior nor the new credential may appear; both
        # collapse to the redacted sentinel while keeping the
        # ``this-field-changed`` signal so the audit row is still
        # informative.
        assert inner == {
            "api_key": {"old": "<redacted>", "new": "<redacted>"},
        }
        rendered = repr(inner)
        assert "old-secret-do-not-leak" not in rendered
        assert "new-secret-do-not-leak" not in rendered

    def test_sensitive_fields_complete_against_provider_config(self) -> None:
        """No credential-bearing field on ProviderConfig is left unredacted.

        Backstops the import-time
        ``_assert_sensitive_fields_complete`` guard: if a future
        rename or new field slips past the heuristic, this test
        flags it via the explicit name list rather than a startup
        crash.
        """
        from synthorg.providers.management.service import (
            _SENSITIVE_PROVIDER_FIELDS,
        )

        credential_suffixes = ("_key", "_token", "_secret", "_password")
        suspected = {
            name
            for name in ProviderConfig.model_fields
            if name.endswith(credential_suffixes) or "password" in name.lower()
        }
        leaks = suspected - _SENSITIVE_PROVIDER_FIELDS
        assert leaks == set(), (
            f"ProviderConfig field(s) look credential-bearing but are "
            f"missing from _SENSITIVE_PROVIDER_FIELDS: {sorted(leaks)!r}"
        )
