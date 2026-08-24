# module-kind: code
"""Responder selection for the concern-routed clarify-and-propose loop.

A :class:`Responder` captures *who* answers one conversational turn: the
generic Chief of Staff persona, or a concern-routed role agent (its
persona, model, and provider). A :class:`RoutingDecision` pairs a routed
responder with the classifier metadata (topic + confidence) that selected
it, for attribution on the recorded turn and the API response.

The proposer keeps full control of the structured-output discipline
(temperature, token budget, the JSON contract): routing only changes the
identity preamble injected into the prompt, the model id, and the
provider. This keeps the clarify/propose JSON deterministic regardless of
which role agent is speaking.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_persona_body
from synthorg.meta.chief_of_staff._turn_redaction import redact_turn_content
from synthorg.meta.chief_of_staff.enums import ConversationKind, RoutingReason
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.providers.protocol import CompletionProvider, ConnectionSelector
from synthorg.settings.model_ref import ModelRef

# Identity preamble for the generic responder. Reproduces the v1 opening
# line of CONVERSATIONAL_PROPOSE_SYSTEM verbatim so the routing-off path
# is behaviourally unchanged.
GENERIC_RESPONDER_PERSONA: NotBlankStr = NotBlankStr("You are the Chief of Staff.")


class Responder(BaseModel):
    """Who answers one clarify-or-propose turn.

    Attributes:
        persona: Identity preamble injected into the prompt's
            ``{responder_identity}`` slot. The literal Chief of Staff
            line for the generic responder, or a role agent's persona
            body when routed.
        model: Model identifier to call for the decision turn.
        provider_name: The connection serving :attr:`model`. Always set:
            a routed responder takes its agent's binding and the generic
            one takes the operator's ``propose_model`` pair, because a
            model id without a connection names no dispatch target.
        agent_id: Responding role agent id; ``None`` for the generic
            Chief of Staff persona (no attribution).
        role: Responding agent's role; ``None`` when generic.
        name: Responding agent's display name; ``None`` when generic.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    persona: NotBlankStr
    model: NotBlankStr
    provider_name: NotBlankStr | None = None
    agent_id: NotBlankStr | None = None
    role: NotBlankStr | None = None
    name: NotBlankStr | None = None

    @property
    def is_routed(self) -> bool:
        """Whether a role agent (not the generic persona) is answering.

        Returns:
            ``True`` when this responder carries a role-agent identity.
        """
        return self.agent_id is not None


class RoutingDecision(BaseModel):
    """A confident route to a role agent, with classifier metadata.

    Returned by a :class:`~synthorg.meta.chief_of_staff.routing.RoleRouter`
    only when a turn routes to a role agent; a ``None`` route means the
    caller falls back to the generic responder.

    Attributes:
        responder: The routed role-agent responder (always carries an
            ``agent_id``).
        topic: Classified concern label that selected the role.
        confidence: Classifier confidence (0-1) for the topic/role.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    responder: Responder
    topic: NotBlankStr
    confidence: float = Field(ge=0.0, le=1.0)


class RoutingOutcome(BaseModel):
    """The result of a routing attempt: a decision plus why it landed.

    ``decision`` is the routed :class:`RoutingDecision` when a role agent
    was selected, or ``None`` when the turn falls back to the generic
    Chief of Staff. ``reason`` explains the outcome either way (``ROUTED``
    on success, or the specific fallback cause), so the surface can report
    why the generic persona answered.

    Attributes:
        decision: The routed decision, or ``None`` on fallback.
        reason: Why this outcome landed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reason: RoutingReason
    decision: RoutingDecision | None = None

    @model_validator(mode="after")
    def _validate_decision_reason(self) -> RoutingOutcome:
        """Keep ``decision`` and ``reason`` consistent.

        Returns:
            The validated outcome.

        Raises:
            ValueError: When a decision is present without ``ROUTED``, or
                ``ROUTED`` is set without a decision.
        """
        routed = self.reason is RoutingReason.ROUTED
        if routed != (self.decision is not None):
            msg = "RoutingOutcome.decision is set iff reason is ROUTED"
            raise ValueError(msg)
        return self


def generic_responder(*, model: ModelRef) -> Responder:
    """Build the generic Chief of Staff responder.

    Args:
        model: The operator-chosen ``(provider, model)`` pair for
            ``chief_of_staff.propose_model``.

    Returns:
        A responder with no role attribution, naming its own connection.
    """
    return Responder(
        persona=GENERIC_RESPONDER_PERSONA,
        model=NotBlankStr(model.model_id),
        provider_name=NotBlankStr(model.provider),
    )


def responder_for_identity(identity: AgentIdentity) -> Responder:
    """Build a routed responder from a resolved agent identity.

    The persona body (name + role + department) is
    rendered without an untrusted-content directive: the propose prompt
    template appends its own, so a single directive reaches the model.

    Args:
        identity: The role agent that will answer the turn.

    Returns:
        A responder carrying the agent's persona, model, provider, and
        attribution.
    """
    return Responder(
        persona=NotBlankStr(render_agent_persona_body(identity)),
        model=identity.model.model_id,
        provider_name=identity.model.provider,
        agent_id=NotBlankStr(str(identity.id)),
        role=identity.role,
        name=identity.name,
    )


def select_responder(
    routing: RoutingDecision | None, *, propose_model: ModelRef
) -> Responder:
    """Pick the responder for a turn: routed agent, or generic fallback.

    Args:
        routing: A confident route to a role agent, or ``None``.
        propose_model: The generic Chief of Staff model used when not
            routed.

    Returns:
        The routed responder, or the generic Chief of Staff responder.
    """
    if routing is not None:
        return routing.responder
    return generic_responder(model=propose_model)


def mark_conversation_routed(
    conversation: Conversation, routing: RoutingDecision | None
) -> Conversation | None:
    """Return a ``routed`` copy of a still-``direct`` conversation.

    Args:
        conversation: The current conversation header.
        routing: The route taken this turn, or ``None``.

    Returns:
        A copy with ``kind`` advanced to ``routed`` when a route landed
        on a ``direct`` thread; ``None`` when no change is needed (no
        route, or the conversation is already routed/group).
    """
    if routing is None or conversation.kind is not ConversationKind.DIRECT:
        return None
    return conversation.model_copy(update={"kind": ConversationKind.ROUTED})


def build_attributed_assistant_turn(
    *,
    conversation_id: NotBlankStr,
    sequence: int,
    content: NotBlankStr,
    routing: RoutingDecision | None,
    now: datetime,
) -> ConversationTurn:
    """Build an assistant turn, attributed to a routed role agent.

    The turn role stays ``ASSISTANT`` (so the clarification cap keeps
    counting it); attribution is carried in the ``author_*`` /
    ``routed_*`` columns. A generic (unrouted) turn leaves them ``None``.

    Returns:
        The composed (still-unpersisted) assistant turn.
    """
    responder = routing.responder if routing is not None else None
    return ConversationTurn(
        conversation_id=conversation_id,
        sequence=sequence,
        role=ConversationRole.ASSISTANT,
        # Redact-before-persist backstop: a model that echoes a credential
        # despite the out-of-band capture flow must not write it to the
        # transcript. A clean flow never trips this.
        content=NotBlankStr(redact_turn_content(content)),
        author_agent_id=responder.agent_id if responder is not None else None,
        author_name=responder.name if responder is not None else None,
        routed_topic=routing.topic if routing is not None else None,
        routing_confidence=routing.confidence if routing is not None else None,
        created_at=now,
    )


def resolve_responder_provider(
    responder: Responder,
    *,
    connections: ConnectionSelector,
) -> CompletionProvider:
    """Select the connection that serves *responder*'s model.

    Every responder names its own provider: a routed one takes its agent's
    binding, the generic one takes the operator's ``propose_model`` pair.
    There is no shared default to fall back to, because a provider is a
    registered connection with its own credentials and endpoint, so
    substituting one would bill and route somewhere nobody chose.

    Returns:
        The completion provider for the decision call.

    Raises:
        DriverNotRegisteredError: When the named connection is not registered.
    """
    return connections(responder.provider_name or "")


__all__ = [
    "GENERIC_RESPONDER_PERSONA",
    "Responder",
    "RoutingDecision",
    "RoutingOutcome",
    "build_attributed_assistant_turn",
    "generic_responder",
    "mark_conversation_routed",
    "resolve_responder_provider",
    "responder_for_identity",
    "select_responder",
]
