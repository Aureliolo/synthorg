# module-kind: code
"""Capabilities of the model an agent is bound to, resolved for the API.

What an agent's model can do is provider state, not agent state: the same
model serves several agents and its metadata changes when the provider is
re-probed. It is therefore resolved at the response boundary instead of
being persisted onto :class:`~synthorg.config.agent_schema.AgentConfig`,
which round-trips through the settings write/read cycle.

:class:`AgentConfigResponse` lists the agent fields it puts on the wire
explicitly rather than subclassing ``AgentConfig``. Two things follow from
that: a field added to the persisted schema reaches the wire only when someone
adds it here too, and a response can never be mistaken for a persistable
``AgentConfig`` by a type-checker. The same reasoning produced
:class:`~synthorg.providers.management._provider_responses.ProviderResponse`.

``model_capabilities`` is ``None`` for two unrelated reasons, so
``model_capability_status`` names which one applies. Collapsing them would make
a settings-store outage indistinguishable from a stale binding, and the
dashboard would report every agent in the org as pointing at a deleted model.
"""

import builtins
from collections.abc import Mapping, Sequence
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.api.state import AppState
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AGENT_CAPABILITIES_UNAVAILABLE,
    API_AGENT_MODEL_BINDING_UNRESOLVED,
)
from synthorg.settings.state import config_resolver_of

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

# Why a null ``model_capabilities`` carries no capabilities. A consumer that
# reads the null alone cannot tell an agent pointing at a deleted model from a
# whole org whose provider config momentarily could not be read.
type ModelCapabilityStatus = Literal[
    "resolved", "unresolved", "provider_config_unavailable"
]


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
    tier: Literal["expert", "capable", "basic"] | None = Field(
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
            "is not in any configured provider (unassigned or stale binding) "
            "or when provider configuration could not be read"
        ),
    )
    model_capability_status: ModelCapabilityStatus = Field(
        default="unresolved",
        description=(
            "Why model_capabilities is null: 'unresolved' = the binding names "
            "nothing configured, 'provider_config_unavailable' = provider "
            "configuration could not be read so no binding was resolvable"
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


async def providers_for_capabilities(
    app_state: AppState,
) -> Mapping[str, ProviderConfig] | None:
    """Read provider config for the capability projection, tolerating failure.

    Takes the app state rather than a resolver so every endpoint that projects
    capabilities shares one composition point; a caller that resolved its own
    resolver first would be free to skip the failure handling below.

    Model capabilities are derived display data layered onto operations that
    have their own result. On a mutation path the write has already committed
    by the time they are resolved, so letting a settings-store failure
    propagate would report a successful create or reorder as an error and
    invite a duplicate retry; on a read path it would fail a whole payload the
    caller asked for other reasons.

    An empty mapping and ``None`` are different answers: ``{}`` means no
    provider is configured, ``None`` means the question could not be asked.
    Only the latter makes an unresolved binding meaningless.

    The catch is broad because tolerance here cannot be contingent on which
    layer failed: an unwired resolver raises ``ServiceUnavailableError`` and a
    dropped connection surfaces whatever the store raises, neither of which is
    a ``SettingsError``, yet both reach the caller identically. Cancellation
    still propagates, so a caller abandoning the request is not mistaken for an
    outage.

    Args:
        app_state: Application state carrying the config resolver.

    Returns:
        Configured providers, or ``None`` when they cannot be read.

    Raises:
        MemoryError: If the process is out of memory.
        RecursionError: If the recursion limit was exceeded.
    """
    try:
        return await config_resolver_of(app_state).get_provider_configs()
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised above
        logger.warning(
            API_AGENT_CAPABILITIES_UNAVAILABLE,
            error_type=type(exc).__name__,
        )
        return None


def with_model_capabilities(
    agents: Sequence[AgentConfig],
    providers: Mapping[str, ProviderConfig] | None,
) -> tuple[AgentConfigResponse, ...]:
    """Project agents onto the wire with their assigned-model capabilities.

    An agent whose ``(provider, model_id)`` binding matches no configured
    model keeps ``model_capabilities=None`` rather than a fabricated
    all-false summary, so the dashboard can distinguish "no capabilities"
    from "not resolvable".

    A ``None`` *providers* means provider configuration could not be read, so
    no binding is resolvable and every agent reports
    ``provider_config_unavailable``. That case skips the per-agent unresolved
    warning: the bindings are not known to be broken, and logging one line per
    agent would bury the single settings failure that actually happened.

    Args:
        agents: Agent configurations to project.
        providers: Configured providers keyed by name, or ``None`` when
            provider configuration could not be read.

    Returns:
        One response model per agent, in the input order.
    """
    if providers is None:
        return tuple(
            _response(
                agent,
                capabilities=None,
                status="provider_config_unavailable",
            )
            for agent in agents
        )
    index = _metadata_index(providers)
    responses: list[AgentConfigResponse] = []
    for agent in agents:
        capabilities = _resolve_capabilities(agent, index)
        responses.append(
            _response(
                agent,
                capabilities=capabilities,
                status="resolved" if capabilities is not None else "unresolved",
            )
        )
    return tuple(responses)


def _response(
    agent: AgentConfig,
    *,
    capabilities: AgentModelCapabilities | None,
    status: ModelCapabilityStatus,
) -> AgentConfigResponse:
    """Build one wire response for *agent*.

    Args:
        agent: Agent configuration to project.
        capabilities: Resolved capability summary, if any.
        status: Why *capabilities* is or is not populated.

    Returns:
        The agent as the API returns it.
    """
    return AgentConfigResponse(
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
        model_capabilities=capabilities,
        model_capability_status=status,
    )
