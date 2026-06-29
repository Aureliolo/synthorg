"""Closure guard across the three prompt-purpose populations.

The epic keeps three sets in lock-step: the prompt-purpose registry (the source
of ids), the pin registry (a pin per id), and the committed drift golden (a
fingerprint per id). The freshness canary catches a *changed* fingerprint; this
guard catches a *missing* one: a new ``PromptPurposeId`` added without a pin or
a golden refresh, which the per-segment tests would each miss because each only
inspects its own set.
"""

import pytest

from synthorg.hr.evaluation.pin_fingerprint import GOLDEN_PATH, load_pin_golden
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PROMPT_PURPOSE_REGISTRY, PromptPurposeId

pytestmark = pytest.mark.unit


def test_registry_enumerates_the_enum() -> None:
    # The registry is the single id source; it must enumerate exactly the enum.
    registered = {str(purpose.id) for purpose in PROMPT_PURPOSE_REGISTRY.all_purposes()}
    assert registered == {member.value for member in PromptPurposeId}


@pytest.mark.parametrize("purpose", list(PromptPurposeId))
def test_every_purpose_has_a_pin(purpose: PromptPurposeId) -> None:
    pin = pin_for(purpose)
    assert isinstance(pin, ModelPinMetadata)
    assert pin.prompt_class_id == purpose


def test_golden_keys_match_registry_exactly() -> None:
    golden = load_pin_golden(GOLDEN_PATH)
    golden_ids = set(golden)
    registry_ids = {member.value for member in PromptPurposeId}

    missing = registry_ids - golden_ids
    stale = golden_ids - registry_ids
    assert not missing, f"purposes absent from the drift golden: {sorted(missing)}"
    assert not stale, f"stale golden entries with no purpose: {sorted(stale)}"
