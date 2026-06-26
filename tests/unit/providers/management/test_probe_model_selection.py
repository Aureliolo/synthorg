"""Unit tests for the connectivity-probe model-size heuristic.

``_cheapest_probe_model_id`` picks the smallest non-embedding model so a
connection test does not cold-load a heavyweight default; it ranks by
``_estimated_param_billions`` parsed from the model id.
"""

import math

import pytest

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.providers.management.service import (
    _cheapest_probe_model_id,
    _estimated_param_billions,
)

pytestmark = pytest.mark.unit


def _model(model_id: str) -> ProviderModelConfig:
    return ProviderModelConfig(id=model_id, cost_per_1k_input=0.0, max_context=4096)


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        pytest.param("llama3.2:1b", 1.0, id="single-size"),
        pytest.param("gemma4:26b-a4b", 26.0, id="total-params-win-over-moe-active"),
        pytest.param("qwen2.5:72b-instruct", 72.0, id="size-amid-version-and-suffix"),
        pytest.param("plain-model", math.inf, id="no-size-token"),
    ],
)
def test_estimated_param_billions(model_id: str, expected: float) -> None:
    assert _estimated_param_billions(model_id) == expected


def test_estimated_param_billions_ignores_glued_version_digit() -> None:
    # A cloud id whose name embeds a digit-b (gpt4b) must NOT be misread as a
    # 4-billion local model: only separator-anchored size tokens count, so it
    # falls through to ``inf`` and is never preferred as the cheapest probe.
    assert _estimated_param_billions("gpt4b-turbo") == math.inf


def test_cheapest_probe_prefers_smallest_non_embedding() -> None:
    models = (_model("llama:70b"), _model("llama:7b"), _model("nomic-embed-text"))
    assert _cheapest_probe_model_id(models) == "llama:7b"


def test_cheapest_probe_falls_back_when_only_embedding_models() -> None:
    # Embedding models reject chat completion, so they are skipped; with no
    # better option the first model is used as a last resort.
    models = (_model("nomic-embed-text"), _model("bge-embed"))
    assert _cheapest_probe_model_id(models) == "nomic-embed-text"
