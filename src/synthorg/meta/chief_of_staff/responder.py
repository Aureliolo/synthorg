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

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_persona_body
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry

# Identity preamble for the generic responder. Reproduces the v1 opening
# line of CONVERSATIONAL_PROPOSE_PROMPT verbatim so the routing-off path
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
        provider_name: Provider that serves :attr:`model`; ``None`` for
            the generic responder, which uses the proposer's default
            provider.
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


def generic_responder(*, model: NotBlankStr) -> Responder:
    """Build the generic Chief of Staff responder.

    Args:
        model: Model id to call (the proposer's ``propose_model``).

    Returns:
        A responder with no role attribution and no provider override.
    """
    return Responder(persona=GENERIC_RESPONDER_PERSONA, model=model)


def responder_for_identity(identity: AgentIdentity) -> Responder:
    """Build a routed responder from a resolved agent identity.

    The persona body (role + department + seniority + personality) is
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
    routing: RoutingDecision | None, *, propose_model: NotBlankStr
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
        id=NotBlankStr(str(uuid.uuid4())),
        conversation_id=conversation_id,
        sequence=sequence,
        role=ConversationRole.ASSISTANT,
        content=content,
        author_agent_id=responder.agent_id if responder is not None else None,
        author_name=responder.name if responder is not None else None,
        routed_topic=routing.topic if routing is not None else None,
        routing_confidence=routing.confidence if routing is not None else None,
        created_at=now,
    )


def resolve_responder_provider(
    responder: Responder,
    *,
    default: CompletionProvider,
    registry: ProviderRegistry | None,
) -> CompletionProvider:
    """Select the provider that serves *responder*'s model.

    A routed responder names its agent's own provider; resolve it
    through *registry* so the role agent answers on its configured
    provider. The generic responder (and any routed responder when no
    registry is wired) uses *default*.

    Returns:
        The completion provider for the decision call.
    """
    if responder.provider_name is not None and registry is not None:
        return registry.get(responder.provider_name)
    return default


__all__ = [
    "GENERIC_RESPONDER_PERSONA",
    "Responder",
    "RoutingDecision",
    "build_attributed_assistant_turn",
    "generic_responder",
    "mark_conversation_routed",
    "resolve_responder_provider",
    "responder_for_identity",
    "select_responder",
]
