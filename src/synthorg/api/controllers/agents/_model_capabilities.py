# module-kind: code
"""Capabilities of the model an agent is bound to, resolved for the API.

What an agent's model can do is provider state, not agent state: the same
model serves several agents and its metadata changes when the provider is
re-probed. It is therefore resolved at the response boundary instead of
being persisted onto :class:`~synthorg.config.agent_schema.AgentConfig`,
which round-trips through the settings write/read cycle.

:class:`AgentConfigResponse` re-declares the agent fields it puts on the wire
rather than subclassing ``AgentConfig``. Two things follow from that: a field
added to the persisted schema reaches the wire only when someone adds it here
too, and a response can never be mistaken for a persistable ``AgentConfig`` by
a type-checker. The same reasoning produced
:class:`~synthorg.providers.management._provider_responses.ProviderResponse`.
"""

from collections.abc import Mapping, Sequence
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AGENT_MODEL_BINDING_UNRESOLVED

logger = get_logger(__name__)

# ``None`` and ``False`` are opposite facts about a model but both falsy, so
# the wire carries a named state instead of the raw tri-state bool the
# provider layer stores.
type ToolCallVerification = Literal["unverified", "verified", "failed"]

_TOOL_CALL_VERIFICATION: Mapping[bool | None, ToolCallVerification] = {
    None: "unverified",
    True: "verified",
    False: "failed",
}


class AgentModelCapabilities(BaseModel):
    """What the model bound to an agent can actually do.

    Tool calling has no capability flag here. The matcher only ever assigns a
    model it believes can call tools, so a positive flag would read the same
    for every agent that holds one. The case where that guarantee goes stale
    afterwards is a runtime failure rather than a capability, and
    ``tool_calling`` reports it separately.

    Attributes:
        supports_reasoning: Model exposes extended reasoning.
        supports_vision: Model accepts image inputs.
        tool_calling: Runtime tool-calling verdict. ``unverified`` = never
            observed, ``verified`` = a real tool call succeeded, ``failed`` =
            runtime proved the model cannot call tools.
        metadata_source: Provenance of the capability data. ``unknown``
            marks a model whose capabilities are assumed, not measured, so
            the dashboard can say so rather than implying it has none.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    supports_reasoning: bool = Field(
        default=False,
        description="Model exposes extended reasoning",
    )
    supports_vision: bool = Field(
        default=False,
        description="Model accepts image inputs",
    )
    tool_calling: ToolCallVerification = Field(
        default="unverified",
        description="Runtime tool-calling verdict",
    )
    metadata_source: MetadataSource = Field(
        default="unknown",
        description="Provenance of the capability data",
    )

    @classmethod
    def from_metadata(cls, metadata: ModelMetadata) -> Self:
        """Project the provider's model metadata onto the agent-facing view.

        Args:
            metadata: Capability metadata of the model the agent is bound to.

        Returns:
            The agent-facing capability summary.
        """
        return cls(
            supports_reasoning=metadata.supports_reasoning,
            supports_vision=metadata.supports_vision,
            tool_calling=_TOOL_CALL_VERIFICATION[metadata.tool_calls_verified],
            metadata_source=metadata.metadata_source,
        )


class AgentConfigResponse(BaseModel):
    """An agent as the API returns it, with its model's real capabilities.

    Mirrors the wire-facing subset of ``AgentConfig``; see the module
    docstring for why the fields are re-declared instead of inherited.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(description="Stable agent id, derived from the name")
    name: NotBlankStr = Field(description="Agent display name")
    role: NotBlankStr = Field(description="Role name")
    department: NotBlankStr = Field(description="Department name")
    personality_preset: NotBlankStr | None = Field(
        default=None,
        description="Named personality preset",
    )
    personality: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw personality config",
    )
    model: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw model config",
    )
    memory: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw memory config",
    )
    tools: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw tools config",
    )
    authority: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw authority config",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Per-agent autonomy level override",
    )
    strategic_output_mode: StrategicOutputMode | None = Field(
        default=None,
        description="Per-agent strategic output mode override",
    )
    tier: Literal["large", "medium", "small"] | None = Field(
        default=None,
        description="Resolved model tier from the setup wizard",
    )
    model_requirement: dict[str, JsonValue] | None = Field(
        default=None,
        description="Raw model requirement dict from the setup wizard",
    )
    model_capabilities: AgentModelCapabilities | None = Field(
        default=None,
        description=(
            "Capabilities of the assigned model; None when the agent's model "
            "is not in any configured provider (unassigned or stale binding)"
        ),
    )


def _metadata_index(
    providers: Mapping[str, ProviderConfig],
) -> dict[tuple[str, str], ModelMetadata]:
    """Index every configured model's metadata by ``(provider, model id)``.

    Aliases are indexed alongside ids because an agent may be bound through
    either form.

    Args:
        providers: Configured providers keyed by name.

    Returns:
        Metadata keyed by provider name and model id or alias.
    """
    index: dict[tuple[str, str], ModelMetadata] = {}
    for provider_name, provider in providers.items():
        for model in provider.models:
            index[provider_name, model.id] = model.metadata
            if model.alias:
                index[provider_name, model.alias] = model.metadata
    return index


def _resolve_capabilities(
    agent: AgentConfig,
    index: Mapping[tuple[str, str], ModelMetadata],
) -> AgentModelCapabilities | None:
    """Resolve one agent's model binding against the configured models.

    Args:
        agent: Agent whose ``model`` dict names the binding.
        index: Metadata keyed by ``(provider, model id or alias)``.

    Returns:
        The capability summary, or ``None`` when the binding names nothing
        configured.
    """
    provider_name = agent.model.get("provider")
    model_id = agent.model.get("model_id")
    if not isinstance(provider_name, str) or not isinstance(model_id, str):
        return None
    if not provider_name or not model_id:
        # A blank pair is the setup wizard's "not assigned yet" sentinel, not
        # a broken reference, so it is not worth an operator's attention.
        return None
    metadata = index.get((provider_name, model_id))
    if metadata is None:
        logger.warning(
            API_AGENT_MODEL_BINDING_UNRESOLVED,
            agent_id=str(agent.id),
            provider_name=provider_name,
            model_id=model_id,
        )
        return None
    return AgentModelCapabilities.from_metadata(metadata)


def with_model_capabilities(
    agents: Sequence[AgentConfig],
    providers: Mapping[str, ProviderConfig],
) -> tuple[AgentConfigResponse, ...]:
    """Project agents onto the wire with their assigned-model capabilities.

    An agent whose ``(provider, model_id)`` binding matches no configured
    model keeps ``model_capabilities=None`` rather than a fabricated
    all-false summary, so the dashboard can distinguish "no capabilities"
    from "not resolvable".

    Args:
        agents: Agent configurations to project.
        providers: Configured providers keyed by name.

    Returns:
        One response model per agent, in the input order.
    """
    index = _metadata_index(providers)
    return tuple(
        AgentConfigResponse(
            id=agent.id,
            name=agent.name,
            role=agent.role,
            department=agent.department,
            personality_preset=agent.personality_preset,
            personality=agent.personality,
            model=agent.model,
            memory=agent.memory,
            tools=agent.tools,
            authority=agent.authority,
            autonomy_level=agent.autonomy_level,
            strategic_output_mode=agent.strategic_output_mode,
            tier=agent.tier,
            model_requirement=agent.model_requirement,
            model_capabilities=_resolve_capabilities(agent, index),
        )
        for agent in agents
    )
