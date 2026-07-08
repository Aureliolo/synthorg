# module-kind: code
"""Purpose-to-tier policy: the design tier each prompt class is pinned to.

A model pin records a **design tier** (``example-{large,medium,small}-001``),
never a vendor model, so the provider-agnostic tenet holds. This module is
the single source of that tier judgement: it maps every
:class:`PromptPurposeId` to a :class:`PromptTierKind` (the cognitive-load
reason) and, through that kind, to a canonical
:class:`~synthorg.budget.model_tier.TierName`.

The taxonomy grounds the judgement in what the prompt asks the model to do:

- ``classify_route_triage`` to ``small``: bounded-output classification,
  routing, triage, and connection probes.
- ``judge_grade_verify`` to ``medium``: evaluative judgements, grading,
  verification, consolidation, and run-time intervention proposals.
- ``synthesise_generate_author`` to ``large``: open-ended synthesis,
  generation, authoring, code modification, and planning.

The pin-validation benchmark (:mod:`synthorg.hr.evaluation.pin_validation_benchmark`)
consumes this policy to validate each prompt class against its pinned tier,
and the Wave 2 per-class ``ModelPinMetadata`` rollout assigns tiers from it.
An import-time drift guard rejects any :class:`PromptPurposeId` missing a
policy entry, mirroring ``_PROMPT_PURPOSE_SPECS`` in
:mod:`synthorg.llm.prompt_purpose`, so a purpose added without a tier fails
at import rather than silently defaulting.
"""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.budget.model_tier import TierName
from synthorg.llm.prompt_purpose import PromptPurposeId


class PromptTierKind(StrEnum):
    """Cognitive-load class of a prompt purpose, grounding its tier.

    The value names the kind of work; :data:`_KIND_TIER` maps each kind
    to exactly one canonical tier so the tier is derived, never assigned
    twice.
    """

    CLASSIFY_ROUTE_TRIAGE = "classify_route_triage"
    JUDGE_GRADE_VERIFY = "judge_grade_verify"
    SYNTHESISE_GENERATE_AUTHOR = "synthesise_generate_author"


def _tier_for_kind(kind: PromptTierKind) -> TierName:
    """Map a cognitive-load kind to its canonical tier.

    A ``match`` with :func:`assert_never` rather than a lookup table so a
    newly-added :class:`PromptTierKind` member without a tier is a mypy
    exhaustiveness error at type-check time, not a runtime ``KeyError``.

    Returns:
        The canonical tier label for *kind*.
    """
    match kind:
        case PromptTierKind.CLASSIFY_ROUTE_TRIAGE:
            return "small"
        case PromptTierKind.JUDGE_GRADE_VERIFY:
            return "medium"
        case PromptTierKind.SYNTHESISE_GENERATE_AUTHOR:
            return "large"
        case _ as unreachable:
            assert_never(unreachable)


class ModelTierAssignment(BaseModel):
    """A prompt purpose's pinned tier and the kind that grounds it."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    purpose_id: PromptPurposeId = Field(description="Stable prompt-class identifier")
    kind: PromptTierKind = Field(description="Cognitive-load class grounding the tier")

    @computed_field
    @property
    def tier(self) -> TierName:
        """Return the canonical tier derived from :attr:`kind`."""
        return _tier_for_kind(self.kind)


_S = PromptTierKind.CLASSIFY_ROUTE_TRIAGE
_M = PromptTierKind.JUDGE_GRADE_VERIFY
_L = PromptTierKind.SYNTHESISE_GENERATE_AUTHOR

_TIER_POLICY_SPECS: Final[tuple[tuple[PromptPurposeId, PromptTierKind], ...]] = (
    (PromptPurposeId.SECURITY_SAFETY_CLASSIFIER, _S),
    (PromptPurposeId.SECURITY_UNCERTAINTY, _S),
    (PromptPurposeId.SECURITY_LLM_EVALUATOR, _M),
    (PromptPurposeId.VISION_VERIFY, _M),
    (PromptPurposeId.RED_TEAM_GROUNDING, _M),
    (PromptPurposeId.RED_TEAM_GROUNDING_ENTAILMENT, _M),
    (PromptPurposeId.MEMORY_RERANK, _S),
    (PromptPurposeId.MEMORY_RETRIEVAL_ROUTE, _S),
    (PromptPurposeId.MEMORY_RETRIEVAL_RETRY, _S),
    (PromptPurposeId.MEMORY_FINE_TUNE_QUERY, _S),
    (PromptPurposeId.MEMORY_CONSOLIDATE, _M),
    (PromptPurposeId.MEMORY_COMPRESS, _M),
    (PromptPurposeId.MEMORY_ABSTRACTIVE, _L),
    (PromptPurposeId.PROCEDURAL_SUCCESS_PROPOSER, _M),
    (PromptPurposeId.PROCEDURAL_PROPOSE, _M),
    (PromptPurposeId.KNOWLEDGE_SYNTHESIS, _L),
    (PromptPurposeId.RESEARCH_TRIAGE, _S),
    (PromptPurposeId.RESEARCH_SYNTHESIS, _L),
    (PromptPurposeId.RESEARCH_PLANNING, _L),
    (PromptPurposeId.COS_ROUTING, _S),
    (PromptPurposeId.COS_PROPOSE, _L),
    (PromptPurposeId.COS_CHAT, _M),
    (PromptPurposeId.COS_NARRATIVE, _M),
    (PromptPurposeId.CHARTER_INTERVIEW, _L),
    (PromptPurposeId.TOOLSMITH_AUTHOR, _L),
    (PromptPurposeId.META_CODE_MODIFICATION, _L),
    (PromptPurposeId.STEERING_PROPOSE, _M),
    (PromptPurposeId.EVOLUTION_PROPOSE, _M),
    (PromptPurposeId.WORKSPACE, _M),
    (PromptPurposeId.INTAKE, _S),
    (PromptPurposeId.VERIFICATION, _M),
    (PromptPurposeId.CLASSIFICATION_LOGICAL_CONTRADICTION, _M),
    (PromptPurposeId.CLASSIFICATION_NUMERICAL_DRIFT, _M),
    (PromptPurposeId.CLASSIFICATION_CONTEXT_OMISSION, _M),
    (PromptPurposeId.CLASSIFICATION_COORDINATION_FAILURE, _M),
    (PromptPurposeId.HR_TRAINING_CURATION, _L),
    (PromptPurposeId.HR_CALIBRATION, _S),
    (PromptPurposeId.HR_EVAL_PATTERN_ANALYSIS, _M),
    (PromptPurposeId.HR_EVAL_FIX_PROPOSAL, _L),
    (PromptPurposeId.CLIENT_REQUIREMENT_GENERATOR, _L),
    (PromptPurposeId.PROVIDERS_TEST_CONNECTION, _S),
    (PromptPurposeId.CONFLICT_JUDGE, _M),
)


def _build_policy() -> Mapping[PromptPurposeId, ModelTierAssignment]:
    """Build the purpose-to-assignment map with a drift guard.

    Returns:
        A read-only map with one :class:`ModelTierAssignment` per
        :class:`PromptPurposeId`.

    Raises:
        ValueError: If a :class:`PromptPurposeId` member has no policy
            entry (a purpose added without a tier), or if a purpose
            appears more than once (a duplicate row would silently win
            and change the pinned tier). Both fail at import.
    """
    counts = Counter(purpose_id for purpose_id, _ in _TIER_POLICY_SPECS)
    duplicates = sorted(str(pid) for pid, count in counts.items() if count > 1)
    if duplicates:
        msg = f"Prompt purposes duplicated in tier policy: {duplicates}"
        raise ValueError(msg)
    policy: dict[PromptPurposeId, ModelTierAssignment] = {
        purpose_id: ModelTierAssignment(purpose_id=purpose_id, kind=kind)
        for purpose_id, kind in _TIER_POLICY_SPECS
    }
    missing = sorted(str(pid) for pid in PromptPurposeId if pid not in policy)
    if missing:
        msg = f"Prompt purposes missing a tier-policy entry: {missing}"
        raise ValueError(msg)
    return MappingProxyType(policy)


_POLICY: Final[Mapping[PromptPurposeId, ModelTierAssignment]] = _build_policy()


def assignment_for_purpose(
    purpose_id: str | PromptPurposeId,
) -> ModelTierAssignment:
    """Return the tier assignment for a prompt purpose.

    Args:
        purpose_id: A :class:`PromptPurposeId` member or its string value.

    Returns:
        The :class:`ModelTierAssignment` for that purpose.

    Raises:
        ValueError: If ``purpose_id`` is not a registered purpose (the
            ``PromptPurposeId(...)`` coercion rejects an unknown value).
    """
    key = PromptPurposeId(str(purpose_id))
    return _POLICY[key]


def tier_for_purpose(purpose_id: str | PromptPurposeId) -> TierName:
    """Return the pinned tier for a prompt purpose.

    Returns:
        The canonical tier label.
    """
    return assignment_for_purpose(purpose_id).tier


def tier_model_id(tier: TierName) -> str:
    """Return the vendor-agnostic archetype model id for a tier.

    Returns:
        The ``example-<tier>-001`` archetype id ``heuristic_tier``
        resolves back to *tier*.
    """
    return f"example-{tier}-001"


def model_id_for_purpose(purpose_id: str | PromptPurposeId) -> str:
    """Return the archetype model id a prompt purpose is pinned to.

    Returns:
        The ``example-<tier>-001`` id for the purpose's pinned tier.
    """
    return tier_model_id(tier_for_purpose(purpose_id))


def tier_policy_entries() -> tuple[ModelTierAssignment, ...]:
    """Return every tier assignment, ordered by purpose id.

    Returns:
        Tuple of :class:`ModelTierAssignment`, ascending by purpose id.
    """
    return tuple(_POLICY[pid] for pid in sorted(_POLICY, key=str))


__all__ = [
    "ModelTierAssignment",
    "PromptTierKind",
    "assignment_for_purpose",
    "model_id_for_purpose",
    "tier_for_purpose",
    "tier_model_id",
    "tier_policy_entries",
]
