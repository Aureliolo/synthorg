# module-kind: code
"""Capabilities of the model an agent is bound to, resolved for the API.

What an agent's model can do is provider state, not agent state: the same
model serves several agents and its metadata changes when the provider is
re-probed. It is therefore resolved at the response boundary instead of
being persisted onto :class:`~synthorg.config.agent_schema.AgentConfig`,
which round-trips through the settings write/read cycle.
"""

from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.provider_schema import ProviderConfig


class AgentModelCapabilities(BaseModel):
    """What the model bound to an agent can actually do.

    Tool calling is deliberately absent. The matcher enforces it as a floor
    for every agent, so a per-agent "supports tools" flag would be constant
    and tell a reader nothing. What still varies is ``tool_calls_verified``,
    which reports the model that *failed* tool calling at runtime.

    Attributes:
        supports_reasoning: Model exposes extended reasoning.
        supports_vision: Model accepts image inputs.
        tool_calls_verified: Runtime tool-calling truth. ``None`` = never
            observed, ``True`` = a real tool call succeeded, ``False`` =
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
    tool_calls_verified: bool | None = Field(
        default=None,
        description="Runtime tool-calling truth (None = never observed)",
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
            tool_calls_verified=metadata.tool_calls_verified,
            metadata_source=metadata.metadata_source,
        )


class AgentConfigResponse(AgentConfig):
    """An agent configuration plus its assigned model's real capabilities."""

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


def with_model_capabilities(
    agents: Sequence[AgentConfig],
    providers: Mapping[str, ProviderConfig],
) -> tuple[AgentConfigResponse, ...]:
    """Attach each agent's assigned-model capabilities to its config.

    An agent whose ``(provider, model_id)`` binding matches no configured
    model keeps ``model_capabilities=None`` rather than a fabricated
    all-false summary, so the dashboard can distinguish "no capabilities" from
    "not resolvable".

    Args:
        agents: Agent configurations to enrich.
        providers: Configured providers keyed by name.

    Returns:
        One response model per agent, in the input order.
    """
    index = _metadata_index(providers)
    enriched: list[AgentConfigResponse] = []
    for agent in agents:
        provider_name = agent.model.get("provider")
        model_id = agent.model.get("model_id")
        metadata = (
            index.get((provider_name, model_id))
            if isinstance(provider_name, str) and isinstance(model_id, str)
            else None
        )
        enriched.append(
            AgentConfigResponse(
                **agent.model_dump(),
                model_capabilities=(
                    AgentModelCapabilities.from_metadata(metadata)
                    if metadata is not None
                    else None
                ),
            )
        )
    return tuple(enriched)
