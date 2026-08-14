# module-kind: code
"""Purpose-to-capability policy: the rung each prompt class is pinned to.

A model pin records a **capability rung**
(``example-{basic,capable,expert}-001``), never a vendor model, so the
provider-agnostic tenet holds. This module is the single source of that
judgement: it maps every :class:`PromptPurposeId` to a
:class:`PromptWorkKind` (the cognitive-load reason) and, through that kind,
to a canonical :class:`~synthorg.core.types.CapabilityLevel`.

The taxonomy grounds the judgement in what the prompt asks the model to do:

- ``classify_route_triage`` to ``basic``: bounded-output classification,
  routing, triage, and connection probes.
- ``judge_grade_verify`` to ``capable``: evaluative judgements, grading,
  verification, consolidation, and run-time intervention proposals.
- ``synthesise_generate_author`` to ``expert``: open-ended synthesis,
  generation, authoring, code modification, and planning.

The pin-validation benchmark (:mod:`synthorg.hr.evaluation.pin_validation_benchmark`)
consumes this policy to validate each prompt class against its pinned rung, and
each prompt class's ``ModelPinMetadata`` takes its rung from here.
An import-time drift guard rejects any :class:`PromptPurposeId` missing a
policy entry, mirroring ``_PROMPT_PURPOSE_SPECS`` in
:mod:`synthorg.llm.prompt_purpose`, so a purpose added without a rung fails
at import rather than silently defaulting.
"""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import CapabilityLevel
from synthorg.llm.prompt_purpose import PromptPurposeId


class PromptWorkKind(StrEnum):
    """Cognitive-load class of a prompt purpose, grounding its rung.

    The value names the kind of work; :func:`_capability_for_kind` maps each
    kind to exactly one canonical rung so the rung is derived, never
    assigned twice.
    """

    CLASSIFY_ROUTE_TRIAGE = "classify_route_triage"
    JUDGE_GRADE_VERIFY = "judge_grade_verify"
    SYNTHESISE_GENERATE_AUTHOR = "synthesise_generate_author"


def _capability_for_kind(kind: PromptWorkKind) -> CapabilityLevel:
    """Map a cognitive-load kind to its canonical rung.

    A ``match`` with :func:`assert_never` rather than a lookup table so a
    newly-added :class:`PromptWorkKind` member without a rung is a mypy
    exhaustiveness error at type-check time, not a runtime ``KeyError``.

    Returns:
        The canonical rung for *kind*.
    """
    match kind:
        case PromptWorkKind.CLASSIFY_ROUTE_TRIAGE:
            return "basic"
        case PromptWorkKind.JUDGE_GRADE_VERIFY:
            return "capable"
        case PromptWorkKind.SYNTHESISE_GENERATE_AUTHOR:
            return "expert"
        case _ as unreachable:
            assert_never(unreachable)


class ModelCapabilityAssignment(BaseModel):
    """A prompt purpose's pinned rung and the kind that grounds it."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    purpose_id: PromptPurposeId = Field(description="Stable prompt-class identifier")
    kind: PromptWorkKind = Field(description="Cognitive-load class grounding the rung")

    @computed_field
    @property
    def capability(self) -> CapabilityLevel:
        """Return the canonical rung derived from :attr:`kind`."""
        return _capability_for_kind(self.kind)


_BASIC = PromptWorkKind.CLASSIFY_ROUTE_TRIAGE
_CAPABLE = PromptWorkKind.JUDGE_GRADE_VERIFY
_EXPERT = PromptWorkKind.SYNTHESISE_GENERATE_AUTHOR

_CAPABILITY_POLICY_SPECS: Final[tuple[tuple[PromptPurposeId, PromptWorkKind], ...]] = (
    (PromptPurposeId.SECURITY_SAFETY_CLASSIFIER, _BASIC),
    (PromptPurposeId.SECURITY_UNCERTAINTY, _BASIC),
    (PromptPurposeId.SECURITY_LLM_EVALUATOR, _CAPABLE),
    (PromptPurposeId.VISION_VERIFY, _CAPABLE),
    (PromptPurposeId.RED_TEAM_GROUNDING, _CAPABLE),
    (PromptPurposeId.RED_TEAM_GROUNDING_ENTAILMENT, _CAPABLE),
    (PromptPurposeId.MEMORY_RERANK, _BASIC),
    (PromptPurposeId.MEMORY_RETRIEVAL_ROUTE, _BASIC),
    (PromptPurposeId.MEMORY_RETRIEVAL_RETRY, _BASIC),
    (PromptPurposeId.MEMORY_FINE_TUNE_QUERY, _BASIC),
    (PromptPurposeId.MEMORY_CONSOLIDATE, _CAPABLE),
    (PromptPurposeId.MEMORY_COMPRESS, _CAPABLE),
    (PromptPurposeId.MEMORY_ABSTRACTIVE, _EXPERT),
    (PromptPurposeId.PROCEDURAL_SUCCESS_PROPOSER, _CAPABLE),
    (PromptPurposeId.PROCEDURAL_PROPOSE, _CAPABLE),
    (PromptPurposeId.KNOWLEDGE_SYNTHESIS, _EXPERT),
    (PromptPurposeId.RESEARCH_TRIAGE, _BASIC),
    (PromptPurposeId.RESEARCH_SYNTHESIS, _EXPERT),
    (PromptPurposeId.RESEARCH_PLANNING, _EXPERT),
    (PromptPurposeId.COS_TURN_INTENT, _BASIC),
    (PromptPurposeId.COS_ROUTING, _BASIC),
    (PromptPurposeId.COS_MULTI_VOICE, _CAPABLE),
    (PromptPurposeId.COS_PROPOSE, _EXPERT),
    (PromptPurposeId.COS_CHAT, _CAPABLE),
    (PromptPurposeId.COS_NARRATIVE, _CAPABLE),
    (PromptPurposeId.CHARTER_INTERVIEW, _EXPERT),
    (PromptPurposeId.TOOLSMITH_AUTHOR, _EXPERT),
    (PromptPurposeId.META_CODE_MODIFICATION, _EXPERT),
    (PromptPurposeId.STEERING_PROPOSE, _CAPABLE),
    (PromptPurposeId.EVOLUTION_PROPOSE, _CAPABLE),
    (PromptPurposeId.PLAN_REVIEW_ITEM_REPLY, _CAPABLE),
    (PromptPurposeId.WORKSPACE, _CAPABLE),
    (PromptPurposeId.INTAKE, _BASIC),
    (PromptPurposeId.VERIFICATION, _CAPABLE),
    (PromptPurposeId.CLASSIFICATION_LOGICAL_CONTRADICTION, _CAPABLE),
    (PromptPurposeId.CLASSIFICATION_NUMERICAL_DRIFT, _CAPABLE),
    (PromptPurposeId.CLASSIFICATION_CONTEXT_OMISSION, _CAPABLE),
    (PromptPurposeId.CLASSIFICATION_COORDINATION_FAILURE, _CAPABLE),
    (PromptPurposeId.HR_TRAINING_CURATION, _EXPERT),
    (PromptPurposeId.HR_CALIBRATION, _BASIC),
    (PromptPurposeId.HR_EVAL_PATTERN_ANALYSIS, _CAPABLE),
    (PromptPurposeId.HR_EVAL_FIX_PROPOSAL, _EXPERT),
    (PromptPurposeId.CLIENT_REQUIREMENT_GENERATOR, _EXPERT),
    (PromptPurposeId.PROVIDERS_TEST_CONNECTION, _BASIC),
    (PromptPurposeId.PROVIDERS_CAPABILITY_CLASSIFICATION, _BASIC),
    (PromptPurposeId.CONFLICT_JUDGE, _CAPABLE),
)


def _build_policy() -> Mapping[PromptPurposeId, ModelCapabilityAssignment]:
    """Build the purpose-to-assignment map with a drift guard.

    Returns:
        A read-only map with one :class:`ModelCapabilityAssignment` per
        :class:`PromptPurposeId`.

    Raises:
        ValueError: If a :class:`PromptPurposeId` member has no policy
            entry (a purpose added without a rung), or if a purpose
            appears more than once (a duplicate row would silently win
            and change the pinned rung). Both fail at import.
    """
    counts = Counter(purpose_id for purpose_id, _ in _CAPABILITY_POLICY_SPECS)
    duplicates = sorted(str(pid) for pid, count in counts.items() if count > 1)
    if duplicates:
        msg = f"Prompt purposes duplicated in capability policy: {duplicates}"
        raise ValueError(msg)
    policy: dict[PromptPurposeId, ModelCapabilityAssignment] = {
        purpose_id: ModelCapabilityAssignment(purpose_id=purpose_id, kind=kind)
        for purpose_id, kind in _CAPABILITY_POLICY_SPECS
    }
    missing = sorted(str(pid) for pid in PromptPurposeId if pid not in policy)
    if missing:
        msg = f"Prompt purposes missing a capability-policy entry: {missing}"
        raise ValueError(msg)
    return MappingProxyType(policy)


_POLICY: Final[Mapping[PromptPurposeId, ModelCapabilityAssignment]] = _build_policy()


def assignment_for_purpose(
    purpose_id: str | PromptPurposeId,
) -> ModelCapabilityAssignment:
    """Return the capability assignment for a prompt purpose.

    Args:
        purpose_id: A :class:`PromptPurposeId` member or its string value.

    Returns:
        The :class:`ModelCapabilityAssignment` for that purpose.

    Raises:
        ValueError: If ``purpose_id`` is not a registered purpose (the
            ``PromptPurposeId(...)`` coercion rejects an unknown value).
    """
    key = PromptPurposeId(str(purpose_id))
    return _POLICY[key]


def capability_for_purpose(purpose_id: str | PromptPurposeId) -> CapabilityLevel:
    """Return the pinned rung for a prompt purpose.

    Returns:
        The canonical rung.
    """
    return assignment_for_purpose(purpose_id).capability


def capability_model_id(capability: CapabilityLevel) -> str:
    """Return the vendor-agnostic archetype model id for a rung.

    Returns:
        The ``example-<rung>-001`` archetype id
        ``heuristic_capability`` resolves back to *capability*.
    """
    return f"example-{capability}-001"


def model_id_for_purpose(purpose_id: str | PromptPurposeId) -> str:
    """Return the archetype model id a prompt purpose is pinned to.

    Returns:
        The ``example-<rung>-001`` id for the purpose's pinned rung.
    """
    return capability_model_id(capability_for_purpose(purpose_id))


def capability_policy_entries() -> tuple[ModelCapabilityAssignment, ...]:
    """Return every capability assignment, ordered by purpose id.

    Returns:
        Tuple of :class:`ModelCapabilityAssignment`, ascending by purpose id.
    """
    return tuple(_POLICY[pid] for pid in sorted(_POLICY, key=str))


__all__ = [
    "ModelCapabilityAssignment",
    "PromptWorkKind",
    "assignment_for_purpose",
    "capability_for_purpose",
    "capability_model_id",
    "capability_policy_entries",
    "model_id_for_purpose",
]
