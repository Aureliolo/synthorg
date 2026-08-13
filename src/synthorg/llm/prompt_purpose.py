# module-kind: code
"""Prompt-purpose registry: the single source of stable prompt-class IDs.

This registry is the single vocabulary of stable :class:`PromptPurposeId`
values for the system prompt classes that wrap an LLM call. It is the
identifier source two planned consumers share: cost attribution (slicing
spend/latency by purpose) and model-pin validation (the value a
:attr:`ModelPinMetadata.prompt_class_id` carries). Holding the ids in one
enum, with a registry that maps each to its category and a human-readable
description, means those consumers reference one vocabulary instead of
inventing or drifting their own purpose strings.

The id values are the static ``system:*`` prefix of the
``cost_recording_scope`` ``task_id`` each prompt class already emits, so
the registry describes purposes the codebase already produces rather than
a parallel naming scheme. Most are ``system:<subsystem>:<purpose>``;
classes whose whole subsystem is one prompt use the two-segment
``system:<subsystem>`` (e.g. ``system:workspace``, ``system:intake``).
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.prompt_purpose import (
    PROMPT_PURPOSE_ALREADY_REGISTERED,
    PROMPT_PURPOSE_NOT_FOUND,
)

logger = get_logger(__name__)


class PromptPurposeCategory(StrEnum):
    """Top-level subsystem a prompt purpose belongs to.

    The value is the ``src/synthorg/`` package directory the prompt class
    lives in, so dashboards can roll spend up by subsystem without a
    separate mapping table.
    """

    SECURITY = "security"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    RESEARCH = "research"
    META = "meta"
    ENGINE = "engine"
    HR = "hr"
    CLIENT = "client"
    PROVIDER = "providers"
    COMMUNICATION = "communication"


class PromptPurposeId(StrEnum):
    """Stable identifier for a system prompt class.

    Values are the static ``system:*`` prefix of the
    ``cost_recording_scope`` ``task_id`` each prompt class already emits;
    any per-invocation suffix is dropped, so the id names the class, not
    the call instance. Most are ``system:<subsystem>:<purpose>``; a class
    that is its subsystem's only prompt uses the two-segment
    ``system:<subsystem>`` form. The id prefix does not encode the
    category: read :class:`PromptPurpose.category` for that.
    """

    SECURITY_SAFETY_CLASSIFIER = "system:security:safety_classifier"
    SECURITY_UNCERTAINTY = "system:security:uncertainty"
    SECURITY_LLM_EVALUATOR = "system:security:llm_evaluator"
    VISION_VERIFY = "system:vision_verify"
    RED_TEAM_GROUNDING = "system:red_team:grounding"
    RED_TEAM_GROUNDING_ENTAILMENT = "system:red_team:grounding_entailment"

    MEMORY_RERANK = "system:memory:rerank"
    MEMORY_RETRIEVAL_ROUTE = "system:memory:retrieval_route"
    MEMORY_RETRIEVAL_RETRY = "system:memory:retrieval_retry"
    MEMORY_FINE_TUNE_QUERY = "system:memory:fine_tune_query"
    MEMORY_CONSOLIDATE = "system:memory:consolidate"
    MEMORY_COMPRESS = "system:memory:compress"
    MEMORY_ABSTRACTIVE = "system:memory:abstractive"
    PROCEDURAL_SUCCESS_PROPOSER = "system:procedural:success_proposer"
    PROCEDURAL_PROPOSE = "system:procedural:propose"

    KNOWLEDGE_SYNTHESIS = "system:knowledge:synthesis"

    RESEARCH_TRIAGE = "system:research:triage"
    RESEARCH_SYNTHESIS = "system:research:synthesis"
    RESEARCH_PLANNING = "system:research:planning"

    COS_TURN_INTENT = "system:cos:turn_intent"
    COS_ROUTING = "system:cos:routing"
    COS_MULTI_VOICE = "system:cos:multi_voice"
    COS_PROPOSE = "system:cos:propose"
    COS_CHAT = "system:cos:chat"
    COS_NARRATIVE = "system:cos:narrative"
    CHARTER_INTERVIEW = "system:charter:interview"
    TOOLSMITH_AUTHOR = "system:toolsmith:author"
    META_CODE_MODIFICATION = "system:meta:code_modification"

    STEERING_PROPOSE = "system:steering:propose"
    EVOLUTION_PROPOSE = "system:evolution:propose"
    PLAN_REVIEW_ITEM_REPLY = "system:plan_review:item_reply"
    WORKSPACE = "system:workspace"
    INTAKE = "system:intake"
    VERIFICATION = "system:verification"
    CLASSIFICATION_LOGICAL_CONTRADICTION = "system:classification:logical_contradiction"
    CLASSIFICATION_NUMERICAL_DRIFT = "system:classification:numerical_drift"
    CLASSIFICATION_CONTEXT_OMISSION = "system:classification:context_omission"
    CLASSIFICATION_COORDINATION_FAILURE = "system:classification:coordination_failure"

    HR_TRAINING_CURATION = "system:hr:training_curation"
    HR_CALIBRATION = "system:hr:calibration"
    HR_EVAL_PATTERN_ANALYSIS = "system:hr:eval_pattern_analysis"
    HR_EVAL_FIX_PROPOSAL = "system:hr:eval_fix_proposal"

    CLIENT_REQUIREMENT_GENERATOR = "system:client:requirement_generator"

    PROVIDERS_TEST_CONNECTION = "system:providers:test_connection"
    PROVIDERS_CAPABILITY_CLASSIFICATION = "system:providers:tier_classification"

    CONFLICT_JUDGE = "system:conflict:judge"


class PromptPurpose(BaseModel):
    """Registered metadata for one prompt purpose."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: PromptPurposeId = Field(description="Stable prompt-class identifier")
    category: PromptPurposeCategory = Field(description="Subsystem family")
    description: NotBlankStr = Field(
        max_length=256,
        description="What the prompt class asks the model to do",
    )


class PromptPurposeRegistry:
    """Registry mapping :class:`PromptPurposeId` to :class:`PromptPurpose`.

    Internal storage is read-only (``MappingProxyType``) and mutated via
    copy-on-write in :meth:`register`, mirroring
    :class:`ExternalBenchmarkRegistry`.
    """

    def __init__(self) -> None:
        self._purposes: MappingProxyType[str, PromptPurpose] = MappingProxyType({})

    def register(self, purpose: PromptPurpose) -> None:
        """Register a prompt purpose by its id.

        Args:
            purpose: Purpose metadata to register.

        Raises:
            ValueError: If a purpose with the same id but different
                metadata is already registered.
        """
        key = str(purpose.id)
        existing = self._purposes.get(key)
        if existing is not None:
            if existing == purpose:
                return
            logger.warning(
                PROMPT_PURPOSE_ALREADY_REGISTERED,
                prompt_purpose_id=key,
                error_type=ValueError.__name__,
            )
            msg = f"Prompt purpose {key!r} already registered with different metadata"
            raise ValueError(msg)
        updated = dict(self._purposes)
        updated[key] = purpose
        self._purposes = MappingProxyType(updated)

    def get(self, purpose_id: str | PromptPurposeId) -> PromptPurpose:
        """Retrieve a purpose by id.

        Args:
            purpose_id: Registered purpose id (enum member or its value).

        Returns:
            The registered :class:`PromptPurpose`.

        Raises:
            KeyError: If the purpose is not registered.
        """
        key = str(purpose_id)
        purpose = self._purposes.get(key)
        if purpose is None:
            logger.warning(
                PROMPT_PURPOSE_NOT_FOUND,
                prompt_purpose_id=key,
                error_type=KeyError.__name__,
            )
            msg = f"Prompt purpose {key!r} not registered"
            raise KeyError(msg)
        return purpose

    def list_ids(self) -> tuple[str, ...]:
        """List all registered purpose ids, sorted.

        Returns:
            Tuple of ``str``.
        """
        return tuple(sorted(self._purposes))

    def all_purposes(self) -> tuple[PromptPurpose, ...]:
        """List all registered purposes, ordered by id.

        Returns:
            Tuple of :class:`PromptPurpose`.
        """
        return tuple(self._purposes[key] for key in self.list_ids())

    def by_category(
        self, category: PromptPurposeCategory | str
    ) -> tuple[PromptPurpose, ...]:
        """List registered purposes in *category*, ordered by id.

        Args:
            category: Subsystem family to filter by (enum member or its
                string value), mirroring :meth:`get`'s lenient input.

        Returns:
            Tuple of :class:`PromptPurpose`.
        """
        return tuple(p for p in self.all_purposes() if p.category == category)

    def __contains__(self, purpose_id: object) -> bool:
        """Return whether *purpose_id* is registered."""
        return isinstance(purpose_id, str) and purpose_id in self._purposes

    def __len__(self) -> int:
        """Return the number of registered purposes."""
        return len(self._purposes)


_PROMPT_PURPOSE_SPECS: Final[
    tuple[tuple[PromptPurposeId, PromptPurposeCategory, str], ...]
] = (
    (
        PromptPurposeId.SECURITY_SAFETY_CLASSIFIER,
        PromptPurposeCategory.SECURITY,
        "Classify whether content is safe before an agent acts on it.",
    ),
    (
        PromptPurposeId.SECURITY_UNCERTAINTY,
        PromptPurposeCategory.SECURITY,
        "Estimate model uncertainty for a security decision.",
    ),
    (
        PromptPurposeId.SECURITY_LLM_EVALUATOR,
        PromptPurposeCategory.SECURITY,
        "Evaluate a security policy question with an LLM judge.",
    ),
    (
        PromptPurposeId.VISION_VERIFY,
        PromptPurposeCategory.SECURITY,
        "Verify a review artefact with a vision model.",
    ),
    (
        PromptPurposeId.RED_TEAM_GROUNDING,
        PromptPurposeCategory.SECURITY,
        "Extract grounding claims from a red-team deliverable.",
    ),
    (
        PromptPurposeId.RED_TEAM_GROUNDING_ENTAILMENT,
        PromptPurposeCategory.SECURITY,
        "Entail extracted claims against the corpus for a grounding verdict.",
    ),
    (
        PromptPurposeId.MEMORY_RERANK,
        PromptPurposeCategory.MEMORY,
        "Rerank retrieved memories for query relevance.",
    ),
    (
        PromptPurposeId.MEMORY_RETRIEVAL_ROUTE,
        PromptPurposeCategory.MEMORY,
        "Route a retrieval query across the memory hierarchy.",
    ),
    (
        PromptPurposeId.MEMORY_RETRIEVAL_RETRY,
        PromptPurposeCategory.MEMORY,
        "Reformulate and retry a failed memory retrieval.",
    ),
    (
        PromptPurposeId.MEMORY_FINE_TUNE_QUERY,
        PromptPurposeCategory.MEMORY,
        "Generate a fine-tuning query for the embedding model.",
    ),
    (
        PromptPurposeId.MEMORY_CONSOLIDATE,
        PromptPurposeCategory.MEMORY,
        "Consolidate raw memories into durable entries.",
    ),
    (
        PromptPurposeId.MEMORY_COMPRESS,
        PromptPurposeCategory.MEMORY,
        "Compress memory artefacts to reclaim context budget.",
    ),
    (
        PromptPurposeId.MEMORY_ABSTRACTIVE,
        PromptPurposeCategory.MEMORY,
        "Produce an abstractive summary of a memory set.",
    ),
    (
        PromptPurposeId.PROCEDURAL_SUCCESS_PROPOSER,
        PromptPurposeCategory.MEMORY,
        "Propose procedural memories from successful runs.",
    ),
    (
        PromptPurposeId.PROCEDURAL_PROPOSE,
        PromptPurposeCategory.MEMORY,
        "Propose a procedural memory from a task trace.",
    ),
    (
        PromptPurposeId.KNOWLEDGE_SYNTHESIS,
        PromptPurposeCategory.KNOWLEDGE,
        "Synthesise a knowledge entry from source material.",
    ),
    (
        PromptPurposeId.RESEARCH_TRIAGE,
        PromptPurposeCategory.RESEARCH,
        "Triage a research brief into actionable directions.",
    ),
    (
        PromptPurposeId.RESEARCH_SYNTHESIS,
        PromptPurposeCategory.RESEARCH,
        "Synthesise research findings into a brief answer.",
    ),
    (
        PromptPurposeId.RESEARCH_PLANNING,
        PromptPurposeCategory.RESEARCH,
        "Plan the steps to answer a research brief.",
    ),
    (
        PromptPurposeId.COS_TURN_INTENT,
        PromptPurposeCategory.META,
        "Classify an operator turn to the org capability that handles it.",
    ),
    (
        PromptPurposeId.COS_ROUTING,
        PromptPurposeCategory.META,
        "Route a chief-of-staff request to a capability.",
    ),
    (
        PromptPurposeId.COS_MULTI_VOICE,
        PromptPurposeCategory.META,
        "Pick specialists to add a grounded chime-in to an answer.",
    ),
    (
        PromptPurposeId.COS_PROPOSE,
        PromptPurposeCategory.META,
        "Propose an organisational change to the operator.",
    ),
    (
        PromptPurposeId.COS_CHAT,
        PromptPurposeCategory.META,
        "Answer an operator question about the organisation.",
    ),
    (
        PromptPurposeId.COS_NARRATIVE,
        PromptPurposeCategory.META,
        "Narrate organisational state for the operator.",
    ),
    (
        PromptPurposeId.CHARTER_INTERVIEW,
        PromptPurposeCategory.META,
        "Interview the operator to draft an org charter.",
    ),
    (
        PromptPurposeId.TOOLSMITH_AUTHOR,
        PromptPurposeCategory.META,
        "Author a new tool definition for the toolsmith.",
    ),
    (
        PromptPurposeId.META_CODE_MODIFICATION,
        PromptPurposeCategory.META,
        "Modify code as part of a self-improvement strategy.",
    ),
    (
        PromptPurposeId.STEERING_PROPOSE,
        PromptPurposeCategory.ENGINE,
        "Propose a steering intervention for a running task.",
    ),
    (
        PromptPurposeId.EVOLUTION_PROPOSE,
        PromptPurposeCategory.ENGINE,
        "Propose an evolution to an agent's behaviour.",
    ),
    (
        PromptPurposeId.PLAN_REVIEW_ITEM_REPLY,
        PromptPurposeCategory.ENGINE,
        "Answer an operator's comment on a plan item as the responsible role.",
    ),
    (
        PromptPurposeId.WORKSPACE,
        PromptPurposeCategory.ENGINE,
        "Answer a semantic query over a task workspace.",
    ),
    (
        PromptPurposeId.INTAKE,
        PromptPurposeCategory.ENGINE,
        "Clarify an incoming request during intake.",
    ),
    (
        PromptPurposeId.VERIFICATION,
        PromptPurposeCategory.ENGINE,
        "Grade a deliverable against quality criteria.",
    ),
    (
        PromptPurposeId.CLASSIFICATION_LOGICAL_CONTRADICTION,
        PromptPurposeCategory.ENGINE,
        "Detect logical contradictions in an execution transcript.",
    ),
    (
        PromptPurposeId.CLASSIFICATION_NUMERICAL_DRIFT,
        PromptPurposeCategory.ENGINE,
        "Detect unverified numerical claims in a transcript.",
    ),
    (
        PromptPurposeId.CLASSIFICATION_CONTEXT_OMISSION,
        PromptPurposeCategory.ENGINE,
        "Detect omitted references the task context required.",
    ),
    (
        PromptPurposeId.CLASSIFICATION_COORDINATION_FAILURE,
        PromptPurposeCategory.ENGINE,
        "Detect multi-agent coordination failures in a transcript.",
    ),
    (
        PromptPurposeId.HR_TRAINING_CURATION,
        PromptPurposeCategory.HR,
        "Curate training examples from agent transcripts.",
    ),
    (
        PromptPurposeId.HR_CALIBRATION,
        PromptPurposeCategory.HR,
        "Sample calibration judgements for performance scoring.",
    ),
    (
        PromptPurposeId.HR_EVAL_PATTERN_ANALYSIS,
        PromptPurposeCategory.HR,
        "Identify cross-agent weakness patterns from evaluation reports.",
    ),
    (
        PromptPurposeId.HR_EVAL_FIX_PROPOSAL,
        PromptPurposeCategory.HR,
        "Propose remediation actions for identified weakness patterns.",
    ),
    (
        PromptPurposeId.CLIENT_REQUIREMENT_GENERATOR,
        PromptPurposeCategory.CLIENT,
        "Generate client requirements for a synthetic project.",
    ),
    (
        PromptPurposeId.PROVIDERS_TEST_CONNECTION,
        PromptPurposeCategory.PROVIDER,
        "Probe a provider connection with a minimal completion.",
    ),
    (
        PromptPurposeId.PROVIDERS_CAPABILITY_CLASSIFICATION,
        PromptPurposeCategory.PROVIDER,
        "Recommend a routing tier for a configured model.",
    ),
    (
        PromptPurposeId.CONFLICT_JUDGE,
        PromptPurposeCategory.COMMUNICATION,
        "Judge which agent position wins a multi-agent conflict.",
    ),
)


def default_prompt_purpose_registry() -> PromptPurposeRegistry:
    """Build a registry seeded from every ``_PROMPT_PURPOSE_SPECS`` entry.

    Returns:
        A :class:`PromptPurposeRegistry` with one entry per spec.

    Raises:
        ValueError: If a :class:`PromptPurposeId` member has no spec entry,
            so an id added without its metadata fails at import rather than
            silently producing an under-seeded registry.
    """
    registry = PromptPurposeRegistry()
    for purpose_id, category, description in _PROMPT_PURPOSE_SPECS:
        registry.register(
            PromptPurpose(id=purpose_id, category=category, description=description)
        )
    missing = [pid for pid in PromptPurposeId if pid not in registry]
    if missing:
        msg = f"Prompt purposes missing a spec entry: {sorted(map(str, missing))}"
        raise ValueError(msg)
    return registry


PROMPT_PURPOSE_REGISTRY: Final[PromptPurposeRegistry] = (
    default_prompt_purpose_registry()
)
