# module-kind: code
"""Declarative per-class model pins: the one source of every class's pin.

A prompt class does not build its own pin. It declares a stable
:class:`PromptPurposeId` and reads :func:`pin_for`, so cost attribution (the
``prompt_class_id`` a class tags its cost scope with), the operator dashboard
(spend/latency sliced by purpose), and the pin-validation benchmark (drift
fingerprint per class) all resolve one definition rather than three that can
drift apart.

What a pin records:

- ``model`` / capability: derived from the purpose-to-capability policy
  (:mod:`synthorg.llm.model_capability_policy`), so the judgement lives in
  one place and the pin restates none of it.
- ``temperature`` / ``top_p``: the prompt class's shipped *design* sampling. The
  deterministic baseline is ``temperature=0.0`` / ``top_p=1.0``; a spec overrides
  it only where the class ships a deliberately non-deterministic default (e.g. a
  conversational or proposing class). Runtime config can tune the live value, but
  the pin records the validated shipped default, not the mutable setting.
- ``max_tokens``: the per-tier output-token *design budget* the pin was evaluated
  against, not a per-call operational ceiling (which is a separate, configurable
  concern).
- ``model_version_pinned_at``: a static committed validation date (:data:`_PINNED_AT`).
  It is excluded from the drift fingerprint, so re-dating the population does not
  itself read as drift.

An import-time guard rejects any :class:`PromptPurposeId` missing a spec,
mirroring the policy and purpose-registry guards, so a purpose added without a
pin fails at import rather than surfacing a ``KeyError`` on first use.
"""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.core.types import CAPABILITY_LADDER, CapabilityLevel, NotBlankStr
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_capability_policy import (
    capability_for_purpose,
    model_id_for_purpose,
)
from synthorg.llm.prompt_purpose import PromptPurposeId

#: Date this per-class pin population was validated against the golden. Advanced
#: by hand when a pin is re-validated; excluded from the drift fingerprint so
#: re-dating it is not itself drift.
_PINNED_AT: Final[datetime] = parse_iso_utc("2026-06-28T00:00:00Z")

#: Deterministic sampling baseline for a system prompt class.
_CANONICAL_TEMPERATURE: Final[float] = 0.0
_CANONICAL_TOP_P: Final[float] = 1.0

#: Per-rung output-token design budget a pin records (powers of two).
_CAPABILITY_MAX_TOKENS: Final[Mapping[CapabilityLevel, int]] = MappingProxyType(
    {
        "basic": 1024,
        "capable": 2048,
        "expert": 4096,
    },
)

# Fail at import (mirroring the policy's drift guard) if a canonical rung is
# added to ``CapabilityLevel`` without a design budget here.
_missing_ceilings = set(CAPABILITY_LADDER) - set(_CAPABILITY_MAX_TOKENS)
if _missing_ceilings:
    msg = f"Capability rungs missing a max-tokens budget: {sorted(_missing_ceilings)}"
    raise ValueError(msg)


class PinSpec(BaseModel):
    """The sampling values a prompt class's pin records.

    Only the values that define the call's behaviour. The model id and rung
    are derived from the purpose-to-capability policy
    (:mod:`synthorg.llm.model_capability_policy`); the token budget is the
    per-rung design ceiling in :data:`_CAPABILITY_MAX_TOKENS`. A spec records
    none of those, so it never restates them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    temperature: float = Field(
        default=_CANONICAL_TEMPERATURE,
        ge=0.0,
        le=2.0,
        description="Shipped design sampling temperature",
    )
    top_p: float = Field(
        default=_CANONICAL_TOP_P,
        ge=0.0,
        le=1.0,
        description="Shipped design nucleus-sampling top-p",
    )


#: Per-purpose pin spec rows. Every :class:`PromptPurposeId` is enumerated so a
#: new purpose without a pin fails the import guard. A bare :class:`PinSpec` is
#: the deterministic baseline; a populated one records a deliberately
#: non-deterministic shipped default sourced from the class's config default.
#: A sequence (not a dict literal) so a duplicated purpose is caught rather than
#: silently collapsed to a single, possibly wrong, entry.
_PIN_SPEC_ROWS: Final[tuple[tuple[PromptPurposeId, PinSpec], ...]] = (
    (PromptPurposeId.SECURITY_SAFETY_CLASSIFIER, PinSpec()),
    (PromptPurposeId.SECURITY_UNCERTAINTY, PinSpec()),
    (PromptPurposeId.SECURITY_LLM_EVALUATOR, PinSpec()),
    (PromptPurposeId.VISION_VERIFY, PinSpec()),
    (PromptPurposeId.RED_TEAM_GROUNDING, PinSpec()),
    (PromptPurposeId.RED_TEAM_GROUNDING_ENTAILMENT, PinSpec()),
    (PromptPurposeId.MEMORY_RERANK, PinSpec()),
    (PromptPurposeId.MEMORY_RETRIEVAL_ROUTE, PinSpec()),
    (PromptPurposeId.MEMORY_RETRIEVAL_RETRY, PinSpec()),
    (PromptPurposeId.MEMORY_FINE_TUNE_QUERY, PinSpec(temperature=0.3)),
    (PromptPurposeId.MEMORY_CONSOLIDATE, PinSpec(temperature=0.3)),
    (PromptPurposeId.MEMORY_COMPRESS, PinSpec(temperature=0.3)),
    (PromptPurposeId.MEMORY_ABSTRACTIVE, PinSpec(temperature=0.3)),
    (PromptPurposeId.PROCEDURAL_SUCCESS_PROPOSER, PinSpec(temperature=0.3)),
    (PromptPurposeId.PROCEDURAL_PROPOSE, PinSpec(temperature=0.3)),
    (PromptPurposeId.KNOWLEDGE_SYNTHESIS, PinSpec()),
    (PromptPurposeId.RESEARCH_TRIAGE, PinSpec()),
    (PromptPurposeId.RESEARCH_SYNTHESIS, PinSpec()),
    (PromptPurposeId.RESEARCH_PLANNING, PinSpec()),
    (PromptPurposeId.COS_TURN_INTENT, PinSpec()),
    (PromptPurposeId.COS_ROUTING, PinSpec()),
    (PromptPurposeId.COS_MULTI_VOICE, PinSpec(temperature=0.5)),
    (PromptPurposeId.COS_PROPOSE, PinSpec(temperature=0.3)),
    (PromptPurposeId.COS_CHAT, PinSpec(temperature=0.7)),
    (PromptPurposeId.COS_NARRATIVE, PinSpec(temperature=0.4)),
    (PromptPurposeId.CHARTER_INTERVIEW, PinSpec(temperature=0.3)),
    (PromptPurposeId.TOOLSMITH_AUTHOR, PinSpec(temperature=0.2)),
    (PromptPurposeId.META_CODE_MODIFICATION, PinSpec(temperature=0.2)),
    (PromptPurposeId.COMPACTION_SUMMARY, PinSpec(temperature=0.3)),
    (PromptPurposeId.STEERING_PROPOSE, PinSpec(temperature=0.1)),
    (PromptPurposeId.EVOLUTION_PROPOSE, PinSpec(temperature=0.3)),
    (PromptPurposeId.PLAN_REVIEW_ITEM_REPLY, PinSpec(temperature=0.3)),
    (PromptPurposeId.WORKSPACE, PinSpec(temperature=0.1)),
    (PromptPurposeId.INTAKE, PinSpec()),
    (PromptPurposeId.VERIFICATION, PinSpec()),
    (PromptPurposeId.CLASSIFICATION_LOGICAL_CONTRADICTION, PinSpec()),
    (PromptPurposeId.CLASSIFICATION_NUMERICAL_DRIFT, PinSpec()),
    (PromptPurposeId.CLASSIFICATION_CONTEXT_OMISSION, PinSpec()),
    (PromptPurposeId.CLASSIFICATION_COORDINATION_FAILURE, PinSpec()),
    (PromptPurposeId.CLIENT_REQUIREMENT_GENERATOR, PinSpec(temperature=0.7)),
    (PromptPurposeId.PROVIDERS_TEST_CONNECTION, PinSpec()),
    (PromptPurposeId.PROVIDERS_CAPABILITY_CLASSIFICATION, PinSpec()),
    (PromptPurposeId.CONFLICT_JUDGE, PinSpec()),
)


def _build_pin_specs() -> Mapping[PromptPurposeId, PinSpec]:
    """Build the per-purpose pin-spec map with import-time drift guards.

    Returns:
        A read-only map with one :class:`PinSpec` per :class:`PromptPurposeId`.

    Raises:
        ValueError: If a :class:`PromptPurposeId` appears more than once (a
            duplicate row would silently win and change the pin), or if a
            member has no pin spec (a purpose added without a pin). Both fail
            at import.
    """
    counts = Counter(pid for pid, _ in _PIN_SPEC_ROWS)
    duplicates = sorted(str(pid) for pid, count in counts.items() if count > 1)
    if duplicates:
        msg = f"Prompt purposes duplicated in pin specs: {duplicates}"
        raise ValueError(msg)
    specs: dict[PromptPurposeId, PinSpec] = dict(_PIN_SPEC_ROWS)
    missing = sorted(str(pid) for pid in PromptPurposeId if pid not in specs)
    if missing:
        msg = f"Prompt purposes missing a pin spec: {missing}"
        raise ValueError(msg)
    return MappingProxyType(specs)


_PIN_SPECS: Final[Mapping[PromptPurposeId, PinSpec]] = _build_pin_specs()


def pin_for(purpose_id: str | PromptPurposeId) -> ModelPinMetadata:
    """Build the model pin for a prompt class.

    Args:
        purpose_id: A :class:`PromptPurposeId` member or its string value.

    Returns:
        The :class:`ModelPinMetadata` pinning the purpose's policy rung, its
        shipped design sampling, and the rung's output-token design budget.

    Raises:
        ValueError: If ``purpose_id`` is not a registered purpose (the
            ``PromptPurposeId(...)`` coercion rejects an unknown value).
    """
    pid = PromptPurposeId(str(purpose_id))
    spec = _PIN_SPECS[pid]
    capability: CapabilityLevel = capability_for_purpose(pid)
    return ModelPinMetadata(
        prompt_class_id=pid,
        model=NotBlankStr(model_id_for_purpose(pid)),
        model_version_pinned_at=_PINNED_AT,
        temperature=spec.temperature,
        top_p=spec.top_p,
        max_tokens=_CAPABILITY_MAX_TOKENS[capability],
    )


__all__ = [
    "PinSpec",
    "pin_for",
]
