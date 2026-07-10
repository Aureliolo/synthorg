"""Unit tests for LiteLLM pricing extraction, back-fill, and registration."""

import litellm as _litellm
import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers import capability_enrichment
from synthorg.providers.drivers import litellm_model_info
from synthorg.providers.drivers.litellm_model_info import (
    extract_model_pricing,
    register_operator_model_pricing,
)
from synthorg.providers.enums import AuthType

pytestmark = pytest.mark.unit


def test_extract_pricing_scales_per_token_to_per_1k() -> None:
    info = {"input_cost_per_token": 0.000003, "output_cost_per_token": 0.000015}
    assert extract_model_pricing(info) == (0.003, 0.015)


def test_extract_pricing_absent_is_zero() -> None:
    assert extract_model_pricing({}) == (0.0, 0.0)


def test_extract_pricing_non_numeric_is_zero() -> None:
    assert extract_model_pricing({"input_cost_per_token": "n/a"}) == (0.0, 0.0)


def test_enrich_backfills_cost_from_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_model_info,
        "get_litellm_model_info",
        lambda _mid: {
            "input_cost_per_token": 0.000002,
            "output_cost_per_token": 0.000008,
        },
    )
    models = (ProviderModelConfig(id="cloud-x", metadata=ModelMetadata()),)
    result = capability_enrichment._enrich_unknown_via_litellm(models, "openai")
    assert result[0].cost_per_1k_input == 0.002
    assert result[0].cost_per_1k_output == 0.008


def test_enrich_preserves_operator_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_model_info,
        "get_litellm_model_info",
        lambda _mid: {"input_cost_per_token": 0.1, "output_cost_per_token": 0.1},
    )
    models = (
        ProviderModelConfig(
            id="cloud-x",
            cost_per_1k_input=0.5,
            cost_per_1k_output=0.5,
            metadata=ModelMetadata(),
        ),
    )
    result = capability_enrichment._enrich_unknown_via_litellm(models, "openai")
    # An explicit operator price is authoritative and is not overwritten.
    assert result[0].cost_per_1k_input == 0.5
    assert result[0].cost_per_1k_output == 0.5


def test_register_operator_pricing_scales_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, dict[str, object]] = {}

    def _fake_register(entries: dict[str, dict[str, object]]) -> None:
        captured.update(entries)

    monkeypatch.setattr(_litellm, "register_model", _fake_register)
    providers = {
        "gw": ProviderConfig(
            driver="litellm",
            litellm_provider="openai",
            auth_type=AuthType.NONE,
            models=(
                ProviderModelConfig(
                    id="gpt-5.1-chat",
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.006,
                ),
                ProviderModelConfig(id="free-model"),
            ),
        ),
        "scripted": ProviderConfig(
            driver="scripted",
            auth_type=AuthType.NONE,
            models=(ProviderModelConfig(id="fake", cost_per_1k_input=1.0),),
        ),
    }

    register_operator_model_pricing(providers)

    assert "gpt-5.1-chat" in captured
    assert captured["gpt-5.1-chat"]["input_cost_per_token"] == pytest.approx(0.000003)
    assert captured["gpt-5.1-chat"]["litellm_provider"] == "openai"
    # A zero-cost model has nothing to register; a scripted-driver model is
    # never pushed into the LiteLLM cloud pricing database.
    assert "free-model" not in captured
    assert "fake" not in captured
