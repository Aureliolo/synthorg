"""Unit tests for the best-effort model-capability classifier."""

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.types import CapabilityLevel
from synthorg.providers.capability_assignment.classifier import (
    HeuristicCapabilityClassifier,
    classify_model_capability,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("cost_tier", "expected"),
    [(1, "basic"), (2, "capable"), (3, "expert"), (4, "expert")],
)
def test_cost_tier_drives_classification(
    cost_tier: int, expected: CapabilityLevel
) -> None:
    meta = ModelMetadata(cost_tier=cost_tier, metadata_source="probe")
    result = classify_model_capability(meta, model_id="vendor-x", total_cost_per_1k=0.0)
    assert result.capability == expected
    assert result.confidence >= 0.9
    assert "cost_tier" in result.reason


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (7_000_000_000, "basic"),
        (30_000_000_000, "capable"),
        (120_000_000_000, "expert"),
    ],
)
def test_parameter_count_bands(params: int, expected: CapabilityLevel) -> None:
    meta = ModelMetadata(parameter_count=params, metadata_source="probe")
    result = classify_model_capability(meta, model_id="vendor-x", total_cost_per_1k=0.0)
    assert result.capability == expected
    assert "parameter_count" in result.reason


def test_cost_tier_wins_over_parameter_count() -> None:
    # A high parameter count with an authoritative low cost_tier stays basic:
    # the scraped band is the stronger signal and is tried first.
    meta = ModelMetadata(
        cost_tier=1,
        parameter_count=200_000_000_000,
        metadata_source="probe",
    )
    assert (
        classify_model_capability(
            meta, model_id="vendor-x", total_cost_per_1k=5.0
        ).capability
        == "basic"
    )


@pytest.mark.parametrize(
    ("cost", "expected"),
    [(0.0005, "basic"), (0.005, "capable"), (0.05, "expert")],
)
def test_cost_proxy_for_paid_unknown_model(
    cost: float, expected: CapabilityLevel
) -> None:
    meta = ModelMetadata(metadata_source="litellm")
    result = classify_model_capability(
        meta, model_id="vendor-x", total_cost_per_1k=cost
    )
    assert result.capability == expected
    assert "cost_per_1k" in result.reason


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("example-basic-001", "basic"),
        ("example-capable-001", "capable"),
        ("example-expert-001", "expert"),
        # Locality rides alongside the rung rather than replacing it.
        ("example-local-basic-001", "basic"),
    ],
)
def test_archetype_id_is_authoritative(
    model_id: str, expected: CapabilityLevel
) -> None:
    # The canonical example-<rung> archetype id wins over cost, which would
    # otherwise disagree.
    meta = ModelMetadata(metadata_source="unknown")
    result = classify_model_capability(meta, model_id=model_id, total_cost_per_1k=99.0)
    assert result.capability == expected


def test_an_operator_id_is_not_read_as_an_archetype() -> None:
    """Matching is anchored on ``example-``, so a real id falls through.

    A vendor id merely containing a rung word says nothing about capability;
    classifying on it would let a naming coincidence decide routing.
    """
    meta = ModelMetadata(metadata_source="unknown")
    result = classify_model_capability(
        meta,
        model_id="provider-local-basic-001",
        total_cost_per_1k=99.0,
    )
    assert "archetype" not in result.reason


def test_free_unknown_model_defaults_to_capable_low_confidence() -> None:
    # A local/free model with no capability signal is not demoted to basic for
    # being free; it lands on the neutral best-effort default.
    meta = ModelMetadata(metadata_source="unknown")
    result = classify_model_capability(meta, model_id="vendor-x", total_cost_per_1k=0.0)
    assert result.capability == "capable"
    assert result.confidence <= 0.3
    assert "default" in result.reason


def test_heuristic_classifier_reads_model_config_cost() -> None:
    model = ProviderModelConfig(
        id="paid-mystery-001",
        cost_per_1k_input=0.006,
        cost_per_1k_output=0.006,
        metadata=ModelMetadata(metadata_source="litellm"),
    )
    # total 0.012 >= the expert cost floor 0.01
    assert HeuristicCapabilityClassifier().classify(model).capability == "expert"
