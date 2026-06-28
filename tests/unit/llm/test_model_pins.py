"""Unit tests for ``synthorg.llm.model_pins``.

Covers the per-class pin registry: completeness over every ``PromptPurposeId``,
the derived model / tier-budget fields, the shipped sampling overrides, and the
str-coercion + rejection paths of :func:`pin_for`.
"""

import pytest
from pydantic import ValidationError

from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import (
    _PIN_SPECS,
    _PINNED_AT,
    _TIER_MAX_TOKENS,
    PinSpec,
    pin_for,
)
from synthorg.llm.model_tier_policy import (
    model_id_for_purpose,
    tier_for_purpose,
)
from synthorg.llm.prompt_purpose import PromptPurposeId

pytestmark = pytest.mark.unit


def test_every_purpose_has_a_spec() -> None:
    assert set(_PIN_SPECS) == set(PromptPurposeId)


@pytest.mark.parametrize("purpose", list(PromptPurposeId))
def test_pin_for_returns_valid_metadata(purpose: PromptPurposeId) -> None:
    pin = pin_for(purpose)
    assert isinstance(pin, ModelPinMetadata)
    assert pin.prompt_class_id == purpose
    assert pin.model == model_id_for_purpose(purpose)
    assert pin.model_version_pinned_at == _PINNED_AT
    assert pin.max_tokens == _TIER_MAX_TOKENS[tier_for_purpose(purpose)]


def test_pin_for_accepts_str() -> None:
    assert pin_for("system:cos:chat").prompt_class_id == PromptPurposeId.COS_CHAT


def test_pin_for_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="not a valid PromptPurposeId"):
        pin_for("system:does:not:exist")


@pytest.mark.parametrize(
    ("purpose", "temperature"),
    [
        (PromptPurposeId.COS_CHAT, 0.7),
        (PromptPurposeId.COS_NARRATIVE, 0.4),
        (PromptPurposeId.HR_CALIBRATION, 0.3),
        (PromptPurposeId.WORKSPACE, 0.1),
        (PromptPurposeId.MEMORY_RERANK, 0.0),
    ],
)
def test_shipped_temperature_overrides(
    purpose: PromptPurposeId, temperature: float
) -> None:
    assert pin_for(purpose).temperature == pytest.approx(temperature)


def test_top_p_is_unit_by_default() -> None:
    assert all(pin_for(p).top_p == pytest.approx(1.0) for p in PromptPurposeId)


def test_pin_spec_is_frozen() -> None:
    spec = PinSpec()
    with pytest.raises(ValidationError):
        spec.temperature = 0.5  # type: ignore[misc]
