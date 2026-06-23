"""Tests for the shared security-evaluator model selector."""

import pytest
import structlog

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.enums import AuthType
from synthorg.security._model_selection import select_security_eval_model

pytestmark = pytest.mark.unit


def _config(*models: ProviderModelConfig) -> ProviderConfig:
    return ProviderConfig(
        driver="litellm",
        auth_type=AuthType.API_KEY,
        connection_name="provider-test",
        models=models,
        retry=RetryConfig(max_retries=0),
        rate_limiter=RateLimiterConfig(),
    )


class TestSelectSecurityEvalModel:
    def test_explicit_override_wins(self) -> None:
        """An explicit model override short-circuits provider lookup."""
        configs = {"p": _config(ProviderModelConfig(id="ignored-001", alias="large"))}
        assert (
            select_security_eval_model("override-001", configs, "p", event="x.event")
            == "override-001"
        )

    def test_first_model_alias_preferred(self) -> None:
        """With no override, the first configured model's alias is used."""
        configs = {"p": _config(ProviderModelConfig(id="m-001", alias="medium"))}
        assert (
            select_security_eval_model(None, configs, "p", event="x.event") == "medium"
        )

    def test_first_model_id_when_alias_absent(self) -> None:
        """When the first model has no alias, its id is used (the falsy
        ``alias or id`` branch the indirect tests never exercise)."""
        configs = {"p": _config(ProviderModelConfig(id="m-001", alias=None))}
        assert (
            select_security_eval_model(None, configs, "p", event="x.event") == "m-001"
        )

    def test_unknown_provider_falls_back_to_name_and_warns(self) -> None:
        """No config for the provider falls back to the provider name and
        emits the caller-supplied warning event."""
        with structlog.testing.capture_logs() as events:
            result = select_security_eval_model(None, {}, "missing", event="x.event")

        assert result == "missing"
        warnings = [e for e in events if e.get("event") == "x.event"]
        assert warnings, f"expected an x.event warning; got: {events}"
        assert warnings[0]["provider_name"] == "missing"

    def test_provider_with_no_models_falls_back_and_warns(self) -> None:
        """A known provider that has zero configured models also falls
        back to the provider-name hint."""
        with structlog.testing.capture_logs() as events:
            result = select_security_eval_model(
                None, {"p": _config()}, "p", event="x.event"
            )

        assert result == "p"
        assert [e for e in events if e.get("event") == "x.event"]
