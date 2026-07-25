"""Tests for resolving an agent's assigned-model capabilities for the API."""

from typing import Final

import pytest

from synthorg.api.controllers.agents._model_capabilities import (
    AgentConfigResponse,
    AgentModelCapabilities,
    with_model_capabilities,
)
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.enums import AuthType

_PROVIDER: Final[str] = "test-provider"


def _agent(
    name: str, *, provider: str = _PROVIDER, model_id: str = "test-large-001"
) -> AgentConfig:
    """Build an agent bound to ``(provider, model_id)``.

    Returns:
        The agent config.
    """
    return AgentConfig(
        name=name,
        role="Engineer",
        department="engineering",
        model={"provider": provider, "model_id": model_id},
    )


def _provider(*models: ProviderModelConfig) -> dict[str, ProviderConfig]:
    """Build a single-provider mapping around *models*.

    Returns:
        Provider mapping keyed by the test provider name.
    """
    return {_PROVIDER: ProviderConfig(auth_type=AuthType.NONE, models=models)}


def _model(  # noqa: PLR0913 -- keyword-only test factory
    model_id: str,
    *,
    alias: str | None = None,
    reasoning: bool = False,
    vision: bool = False,
    tool_calls_verified: bool | None = None,
    source: str = "probe",
) -> ProviderModelConfig:
    """Build a configured provider model carrying capability metadata.

    Returns:
        The provider model config.
    """
    return ProviderModelConfig(
        id=model_id,
        alias=alias,
        metadata=ModelMetadata(
            supports_tools=True,
            supports_reasoning=reasoning,
            supports_vision=vision,
            tool_calls_verified=tool_calls_verified,
            metadata_source=source,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.unit
class TestWithModelCapabilities:
    def test_resolves_by_model_id(self) -> None:
        agents = [_agent("Ada")]
        providers = _provider(_model("test-large-001", reasoning=True, vision=True))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities == AgentModelCapabilities(
            supports_reasoning=True,
            supports_vision=True,
            tool_calling="unverified",
            metadata_source="probe",
        )

    def test_resolves_by_alias(self) -> None:
        # The index carries aliases because an agent may be bound by either
        # form; binding by alias must resolve exactly as binding by id does.
        agents = [_agent("Ada", model_id="fast")]
        providers = _provider(_model("test-large-001", alias="fast", reasoning=True))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is not None
        assert enriched.model_capabilities.supports_reasoning is True

    def test_unknown_model_id_yields_none(self) -> None:
        agents = [_agent("Ada", model_id="removed-model")]
        providers = _provider(_model("test-large-001"))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is None

    def test_unknown_provider_yields_none(self) -> None:
        agents = [_agent("Ada", provider="retired-provider")]
        providers = _provider(_model("test-large-001"))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is None

    def test_unassigned_agent_yields_none(self) -> None:
        agents = [_agent("Ada", provider="", model_id="")]
        (enriched,) = with_model_capabilities(agents, _provider())
        assert enriched.model_capabilities is None

    def test_malformed_binding_yields_none(self) -> None:
        # A non-string binding degrades rather than raising: the roster must
        # still render when one agent's config is malformed.
        agent = AgentConfig(
            name="Ada",
            role="Engineer",
            department="engineering",
            model={"provider": 7, "model_id": None},
        )
        (enriched,) = with_model_capabilities([agent], _provider())
        assert enriched.model_capabilities is None

    @pytest.mark.parametrize(
        ("verified", "expected"),
        [(None, "unverified"), (True, "verified"), (False, "failed")],
    )
    def test_tool_calling_tri_state(self, verified: bool | None, expected: str) -> None:
        agents = [_agent("Ada")]
        providers = _provider(_model("test-large-001", tool_calls_verified=verified))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is not None
        assert enriched.model_capabilities.tool_calling == expected

    def test_preserves_input_order(self) -> None:
        agents = [_agent("Ada"), _agent("Grace"), _agent("Edsger")]
        enriched = with_model_capabilities(agents, _provider(_model("test-large-001")))
        assert [a.name for a in enriched] == ["Ada", "Grace", "Edsger"]

    def test_carries_agent_fields_through(self) -> None:
        agent = _agent("Ada")
        (enriched,) = with_model_capabilities([agent], _provider())
        assert enriched.id == agent.id
        assert enriched.role == agent.role
        assert enriched.department == agent.department

    def test_never_exposes_provider_secrets(self) -> None:
        # The response rides alongside provider config that is encrypted at
        # rest. Only the four capability fields may cross to the wire, so a
        # future widening to ``**metadata.model_dump()`` fails here first.
        secret = "test-subscription-token"
        providers = {
            _PROVIDER: ProviderConfig(
                auth_type=AuthType.NONE,
                base_url="https://provider.invalid",
                subscription_token=secret,
                models=(_model("test-large-001"),),
            )
        }
        (enriched,) = with_model_capabilities([_agent("Ada")], providers)
        serialised = enriched.model_dump_json()
        assert secret not in serialised
        assert "provider.invalid" not in serialised
        assert enriched.model_capabilities is not None
        assert set(enriched.model_capabilities.model_dump()) == {
            "supports_reasoning",
            "supports_vision",
            "tool_calling",
            "metadata_source",
        }

    def test_response_is_not_a_persistable_agent_config(self) -> None:
        # The response is a sibling of AgentConfig, not a subclass, so a
        # persistence path typed for AgentConfig cannot silently accept one.
        # mypy proves this statically too; the assertion pins it at runtime so
        # a future re-parenting has to break something visible.
        (enriched,) = with_model_capabilities([_agent("Ada")], _provider())
        assert isinstance(enriched, AgentConfigResponse)
        assert AgentConfig not in type(enriched).__mro__
