# module-kind: code
"""The vocabulary a turn's intent is expressed in.

What a turn can be asking for, why it landed there, and the shape a
classifier returns. Separate from the classifier that produces it
(``intent_router.py``) because the consumers outnumber the producer:
the turn controllers, the dispatcher and the state slice all speak this
vocabulary, and none of them wants the provider registry, the cost
recorder and the prompt bodies a classifier drags in.
"""

from enum import StrEnum
from typing import Final, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ConversationTurn


class TurnIntent(StrEnum):
    """Which org capability a single operator turn is asking for.

    Attributes:
        EXPLAIN: Answer a question about the org (read-only). The default
            and the safe fallback for any uncertain classification.
        PROPOSE: Turn a work request into a plan for holistic review.
        ACT: Perform a concrete system action now, via a tool, under the
            acting agent's trust level. Gated behind a stricter floor.
        GROUP_CONVENE: Convene several named agents in a group discussion.
        CHARTER: Interview the operator to draft a company charter.
        CONFIGURE: Configure or operate the control plane through the
            operator console (connect an integration, change a setting,
            call a control-plane tool). Gated behind a stricter floor and
            its own default-off toggle.
    """

    EXPLAIN = "explain"
    PROPOSE = "propose"
    ACT = "act"
    GROUP_CONVENE = "group_convene"
    CHARTER = "charter"
    CONFIGURE = "configure"


class IntentRoutingReason(StrEnum):
    """Why a turn resolved to the intent it did.

    Surfaced on the turn result so a human can see whether the intent was
    classified, forced by an explicit override, fixed by the conversation's
    kind, or degraded to ``EXPLAIN`` because a stricter gate was not met.

    Attributes:
        CLASSIFIED: The classifier's pick was taken as-is.
        EXPLICIT_OVERRIDE: The caller supplied an explicit intent override.
        CONVERSATION_KIND_FIXED: An in-flight GROUP conversation dispatches
            straight to group chat without re-classification, so a follow-up
            turn cannot collapse the thread to EXPLAIN.
        NO_INTENT_CLASSIFIER: No classifier is wired; defaulted to EXPLAIN.
        ACT_FLOOR_NOT_MET: A confident-enough ACT was not reached; degraded
            to EXPLAIN.
        CHARTER_FLOOR_NOT_MET: A confident-enough CHARTER was not reached;
            degraded to EXPLAIN.
        CONFIGURE_FLOOR_NOT_MET: A confident-enough CONFIGURE was not
            reached; degraded to EXPLAIN.
        GROUP_TARGETS_MISSING: A group was requested without enough named
            participants; degraded to EXPLAIN.
        ACT_NO_TARGET: An act was requested without naming an acting agent;
            degraded to EXPLAIN so an ambiguous turn never acts on a guess.
        CLASSIFY_CALL_FAILED: The classifier call errored or timed out;
            defaulted to EXPLAIN.
        RESPONSE_INVALID: The classifier reply failed to parse/validate;
            defaulted to EXPLAIN.
    """

    CLASSIFIED = "classified"
    EXPLICIT_OVERRIDE = "explicit_override"
    CONVERSATION_KIND_FIXED = "conversation_kind_fixed"
    NO_INTENT_CLASSIFIER = "no_intent_classifier"
    ACT_FLOOR_NOT_MET = "act_floor_not_met"
    CHARTER_FLOOR_NOT_MET = "charter_floor_not_met"
    CONFIGURE_FLOOR_NOT_MET = "configure_floor_not_met"
    GROUP_TARGETS_MISSING = "group_targets_missing"
    ACT_NO_TARGET = "act_no_target"
    CLASSIFY_CALL_FAILED = "classify_call_failed"
    RESPONSE_INVALID = "response_invalid"


#: Reasons reached only after a classification call returned a parsed verdict,
#: and therefore the reasons an outcome can name the model it dispatched on.
#: The degrade reasons belong here because the floor gate runs over a verdict
#: that already arrived; the two failure reasons do not, because the call that
#: would have named a model is exactly what did not come back.
MODEL_ATTRIBUTED_REASONS: Final[frozenset[IntentRoutingReason]] = frozenset(
    {
        IntentRoutingReason.CLASSIFIED,
        IntentRoutingReason.ACT_FLOOR_NOT_MET,
        IntentRoutingReason.CHARTER_FLOOR_NOT_MET,
        IntentRoutingReason.CONFIGURE_FLOOR_NOT_MET,
        IntentRoutingReason.GROUP_TARGETS_MISSING,
        IntentRoutingReason.ACT_NO_TARGET,
    }
)


class IntentClassification(BaseModel):
    """Structured output of one intent-classification model turn.

    Attributes:
        intent: The capability the classifier picked.
        confidence: Classifier confidence (0-1) in the pick.
        named_targets: Roles/names the operator explicitly addressed, as
            the classifier read them; empty when none.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    confidence: float = Field(ge=0.0, le=1.0)
    named_targets: tuple[NotBlankStr, ...] = ()


class IntentOutcome(BaseModel):
    """The resolved intent for a turn, plus why it landed.

    Attributes:
        intent: The capability the turn dispatches to.
        reason: Why this intent was chosen (classified, overridden, fixed
            by conversation kind, or degraded).
        confidence: Classifier confidence (0-1) when a classification ran;
            ``None`` for an override / fixed-kind / no-classifier outcome.
        named_targets: Roles/names surfaced by the classifier for a group
            convene; empty otherwise.
        model: The model id the classification actually dispatched on, which
            is the live pair rather than the one bound at build time; ``None``
            when no classification ran. Carried so the decision log names the
            model that produced the verdict: diagnosing a misrouted turn means
            knowing which model answered, and a build-time pair can be stale.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    reason: IntentRoutingReason
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    named_targets: tuple[NotBlankStr, ...] = ()
    model: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_model_attribution(self) -> Self:
        """Keep the model attribution consistent with ``reason``.

        The model is recorded from the verdict the call returned, so it is
        present exactly for the reasons a verdict reached, and absent for an
        override, a fixed kind, an absent classifier, or a call that failed.
        Mirrors :meth:`ProposeResult._validate_routing_attribution` so a
        construction bug cannot log a model against a decision no model made,
        which is worse than logging none: it names an innocent model as the
        cause of a misroute.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When the attribution and ``reason`` disagree.
        """
        dispatched = self.reason in MODEL_ATTRIBUTED_REASONS
        if dispatched and self.model is None:
            msg = f"model is required when reason is {self.reason.value}"
            raise ValueError(msg)
        if not dispatched and self.model is not None:
            msg = f"model must be absent when reason is {self.reason.value}"
            raise ValueError(msg)
        return self


@runtime_checkable
class IntentClassifier(Protocol):
    """Classifies one operator turn to a :class:`TurnIntent`.

    Implementations are best-effort: :meth:`classify` always returns an
    :class:`IntentOutcome`. Any uncertainty (classifier error, invalid
    reply, a below-floor ACT/CHARTER, a group without enough targets)
    yields ``EXPLAIN`` with the reason it landed there.
    """

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        """Classify the latest human turn to a capability intent.

        Args:
            history: Conversation turns oldest-first, ending with the human
                turn to classify.

        Returns:
            The resolved :class:`IntentOutcome`.
        """
        ...


__all__ = [
    "MODEL_ATTRIBUTED_REASONS",
    "IntentClassification",
    "IntentClassifier",
    "IntentOutcome",
    "IntentRoutingReason",
    "TurnIntent",
]
