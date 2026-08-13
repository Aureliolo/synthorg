"""Unit tests for the purpose-to-tier policy.

Covers completeness (every ``PromptPurposeId`` mapped), the import-time
drift guard, canonical tier values, the archetype model-id round-trip
through ``heuristic_capability``, and a sample of grounded assignments.
"""

import pytest

from synthorg.budget.model_capability import TIERS, heuristic_capability
from synthorg.llm import model_tier_policy
from synthorg.llm.model_capability_policy import (
    PromptTierKind,
    assignment_for_purpose,
    capability_for_purpose,
    capability_model_id,
    capability_policy_entries,
    model_id_for_purpose,
)
from synthorg.llm.prompt_purpose import PromptPurposeId

pytestmark = pytest.mark.unit

# The policy assigns only quality tiers, never the local archetype.
_PROMPT_TIERS = frozenset({"large", "medium", "small"})


def test_every_purpose_has_an_assignment() -> None:
    for purpose_id in PromptPurposeId:
        assignment = assignment_for_purpose(purpose_id)
        assert assignment.purpose_id == purpose_id


def test_policy_entries_cover_all_purposes_sorted() -> None:
    entries = capability_policy_entries()
    assert len(entries) == len(list(PromptPurposeId))
    ids = [str(entry.purpose_id) for entry in entries]
    assert ids == sorted(ids)


def test_all_tiers_are_canonical_quality_tiers() -> None:
    for purpose_id in PromptPurposeId:
        tier = capability_for_purpose(purpose_id)
        assert tier in _PROMPT_TIERS
        assert tier in TIERS


def test_kind_determines_tier() -> None:
    for entry in capability_policy_entries():
        expected = {
            PromptTierKind.CLASSIFY_ROUTE_TRIAGE: "small",
            PromptTierKind.JUDGE_GRADE_VERIFY: "medium",
            PromptTierKind.SYNTHESISE_GENERATE_AUTHOR: "large",
        }[entry.kind]
        assert entry.tier == expected


@pytest.mark.parametrize("tier", ["large", "medium", "small"])
def test_capability_model_id_round_trips_through_heuristic(tier: str) -> None:
    model_id = capability_model_id(tier)  # type: ignore[arg-type]
    assert model_id == f"example-{tier}-001"
    assert heuristic_capability(model_id) == tier


def test_model_id_for_purpose_matches_tier() -> None:
    for purpose_id in PromptPurposeId:
        model_id = model_id_for_purpose(purpose_id)
        assert heuristic_capability(model_id) == capability_for_purpose(purpose_id)


def test_assignment_accepts_str_and_enum() -> None:
    via_enum = assignment_for_purpose(PromptPurposeId.MEMORY_RERANK)
    via_str = assignment_for_purpose("system:memory:rerank")
    assert via_enum == via_str


def test_assignment_rejects_unknown_purpose() -> None:
    with pytest.raises(ValueError, match="system:not:a:purpose"):
        assignment_for_purpose("system:not:a:purpose")


def test_duplicate_policy_entries_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # A purpose repeated in the spec table must fail the import-time build,
    # not silently let the later row win and change the pinned tier.
    monkeypatch.setattr(
        model_tier_policy,
        "_TIER_POLICY_SPECS",
        (
            *model_tier_policy._TIER_POLICY_SPECS,
            (PromptPurposeId.MEMORY_RERANK, PromptTierKind.JUDGE_GRADE_VERIFY),
        ),
    )
    with pytest.raises(ValueError, match="duplicated in tier policy"):
        model_tier_policy._build_policy()


@pytest.mark.parametrize(
    ("purpose_id", "expected_tier"),
    [
        (PromptPurposeId.SECURITY_SAFETY_CLASSIFIER, "small"),
        (PromptPurposeId.MEMORY_RERANK, "small"),
        (PromptPurposeId.SECURITY_LLM_EVALUATOR, "medium"),
        (PromptPurposeId.VERIFICATION, "medium"),
        (PromptPurposeId.RESEARCH_SYNTHESIS, "large"),
        (PromptPurposeId.META_CODE_MODIFICATION, "large"),
    ],
)
def test_grounded_sample_assignments(
    purpose_id: PromptPurposeId, expected_tier: str
) -> None:
    assert capability_for_purpose(purpose_id) == expected_tier
