"""Unit tests for the purpose-to-capability policy.

Covers completeness (every ``PromptPurposeId`` mapped), the import-time
drift guard, canonical rung values, the archetype model-id round-trip
through ``heuristic_capability``, and a sample of grounded assignments.
"""

import pytest

from synthorg.budget.model_capability import heuristic_capability
from synthorg.core.types import CAPABILITY_LADDER
from synthorg.llm import model_capability_policy
from synthorg.llm.model_capability_policy import (
    PromptWorkKind,
    assignment_for_purpose,
    capability_for_purpose,
    capability_model_id,
    capability_policy_entries,
    model_id_for_purpose,
)
from synthorg.llm.prompt_purpose import PromptPurposeId

pytestmark = pytest.mark.unit


def test_every_purpose_has_an_assignment() -> None:
    for purpose_id in PromptPurposeId:
        assignment = assignment_for_purpose(purpose_id)
        assert assignment.purpose_id == purpose_id


def test_policy_entries_cover_all_purposes_sorted() -> None:
    entries = capability_policy_entries()
    assert len(entries) == len(list(PromptPurposeId))
    ids = [str(entry.purpose_id) for entry in entries]
    assert ids == sorted(ids)


def test_all_assignments_are_canonical_rungs() -> None:
    for purpose_id in PromptPurposeId:
        assert capability_for_purpose(purpose_id) in CAPABILITY_LADDER


def test_kind_determines_capability() -> None:
    for entry in capability_policy_entries():
        expected = {
            PromptWorkKind.CLASSIFY_ROUTE_TRIAGE: "basic",
            PromptWorkKind.JUDGE_GRADE_VERIFY: "capable",
            PromptWorkKind.SYNTHESISE_GENERATE_AUTHOR: "expert",
        }[entry.kind]
        assert entry.capability == expected


@pytest.mark.parametrize("capability", ["expert", "capable", "basic"])
def test_capability_model_id_round_trips_through_heuristic(capability: str) -> None:
    model_id = capability_model_id(capability)  # type: ignore[arg-type]
    assert model_id == f"example-{capability}-001"
    assert heuristic_capability(model_id) == capability


def test_model_id_for_purpose_matches_capability() -> None:
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
    # not silently let the later row win and change the pinned rung.
    monkeypatch.setattr(
        model_capability_policy,
        "_CAPABILITY_POLICY_SPECS",
        (
            *model_capability_policy._CAPABILITY_POLICY_SPECS,
            (PromptPurposeId.MEMORY_RERANK, PromptWorkKind.JUDGE_GRADE_VERIFY),
        ),
    )
    with pytest.raises(ValueError, match="duplicated in capability policy"):
        model_capability_policy._build_policy()


@pytest.mark.parametrize(
    ("purpose_id", "expected"),
    [
        (PromptPurposeId.SECURITY_SAFETY_CLASSIFIER, "basic"),
        (PromptPurposeId.MEMORY_RERANK, "basic"),
        (PromptPurposeId.SECURITY_LLM_EVALUATOR, "capable"),
        (PromptPurposeId.VERIFICATION, "capable"),
        (PromptPurposeId.RESEARCH_SYNTHESIS, "expert"),
        (PromptPurposeId.META_CODE_MODIFICATION, "expert"),
    ],
)
def test_grounded_sample_assignments(
    purpose_id: PromptPurposeId, expected: str
) -> None:
    assert capability_for_purpose(purpose_id) == expected
