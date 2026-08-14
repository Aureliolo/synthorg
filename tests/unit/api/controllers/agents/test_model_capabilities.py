"""Tests for resolving an agent's assigned-model capabilities for the API."""

import asyncio
import logging
from typing import Final
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.agents._model_capabilities import (
    AgentConfigResponse,
    AgentModelCapabilities,
    providers_for_capabilities,
    with_model_capabilities,
)
from synthorg.api.state import AppState
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability.events.api import API_AGENT_MODEL_BINDING_UNRESOLVED
from synthorg.providers.enums import AuthType
from synthorg.settings.errors import SettingsError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

_PROVIDER: Final[str] = "test-provider"


def _app_state(resolver: object) -> AppState:
    """Compose the app state ``providers_for_capabilities`` reads.

    Returns:
        App state carrying *resolver*.
    """
    return make_app_state(config_resolver=resolver)


def _agent(
    name: str, *, provider: str = _PROVIDER, model_id: str = "test-expert-001"
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


def _model(
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
        providers = _provider(_model("test-expert-001", reasoning=True, vision=True))
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
        providers = _provider(_model("test-expert-001", alias="fast", reasoning=True))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is not None
        assert enriched.model_capabilities.supports_reasoning is True

    def test_unknown_model_id_yields_none(self) -> None:
        agents = [_agent("Ada", model_id="removed-model")]
        providers = _provider(_model("test-expert-001"))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is None
        assert enriched.model_capability_status == "unresolved"

    def test_unknown_provider_yields_none(self) -> None:
        agents = [_agent("Ada", provider="retired-provider")]
        providers = _provider(_model("test-expert-001"))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is None
        assert enriched.model_capability_status == "unresolved"

    def test_unassigned_agent_yields_none(self) -> None:
        agents = [_agent("Ada", provider="", model_id="")]
        (enriched,) = with_model_capabilities(agents, _provider())
        assert enriched.model_capabilities is None
        assert enriched.model_capability_status == "unresolved"

    def test_resolved_binding_reports_resolved_status(self) -> None:
        providers = _provider(_model("test-expert-001"))
        (enriched,) = with_model_capabilities([_agent("Ada")], providers)
        assert enriched.model_capability_status == "resolved"

    def test_unreadable_provider_config_is_distinct_from_unresolved(self) -> None:
        # The two null cases must stay tellable apart: an org-wide settings
        # failure is not evidence that any one agent's binding is stale.
        agents = [_agent("Ada"), _agent("Grace")]
        enriched = with_model_capabilities(agents, None)
        assert [a.model_capability_status for a in enriched] == [
            "provider_config_unavailable",
            "provider_config_unavailable",
        ]
        assert all(a.model_capabilities is None for a in enriched)

    def test_unreadable_provider_config_does_not_log_per_agent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # One settings failure must not be buried under a warning per agent.
        agents = [_agent(name) for name in ("Ada", "Grace", "Edsger")]
        with caplog.at_level(logging.WARNING):
            with_model_capabilities(agents, None)
        assert API_AGENT_MODEL_BINDING_UNRESOLVED not in caplog.text

    def test_no_providers_configured_is_unresolved_not_unavailable(self) -> None:
        # An empty mapping is a real answer ("nothing configured"); only None
        # means the question could not be asked.
        (enriched,) = with_model_capabilities([_agent("Ada")], {})
        assert enriched.model_capability_status == "unresolved"

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
        providers = _provider(_model("test-expert-001", tool_calls_verified=verified))
        (enriched,) = with_model_capabilities(agents, providers)
        assert enriched.model_capabilities is not None
        assert enriched.model_capabilities.tool_calling == expected

    def test_preserves_input_order(self) -> None:
        agents = [_agent("Ada"), _agent("Grace"), _agent("Edsger")]
        enriched = with_model_capabilities(agents, _provider(_model("test-expert-001")))
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
                models=(_model("test-expert-001"),),
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


@pytest.mark.unit
class TestProvidersForCapabilities:
    """The tolerance boundary around the provider read.

    Callers layer this onto work that has its own result, several of them
    after a committed write, so what escapes matters more than what it
    returns.
    """

    @pytest.mark.parametrize(
        "failure",
        [
            SettingsError(),
            # Raised by ``config_resolver_of`` itself when the resolver is
            # unwired, so it is reachable before the read even starts.
            ServiceUnavailableError("Config Resolver not configured"),
            # Stands in for whatever a dropped store connection surfaces:
            # the caller cannot act on the distinction either way.
            OSError("connection reset"),
        ],
    )
    async def test_ordinary_failures_are_tolerated(self, failure: Exception) -> None:
        resolver = mock_of[ConfigResolver](
            get_provider_configs=AsyncMock(side_effect=failure)
        )

        assert await providers_for_capabilities(_app_state(resolver)) is None

    @pytest.mark.parametrize("critical", [MemoryError, RecursionError])
    async def test_critical_failures_still_propagate(
        self, critical: type[BaseException]
    ) -> None:
        # Degrading the projection is worth it; masking an exhausted process
        # is not, so these keep escaping the best-effort boundary.
        resolver = mock_of[ConfigResolver](
            get_provider_configs=AsyncMock(side_effect=critical())
        )

        with pytest.raises(critical):
            await providers_for_capabilities(_app_state(resolver))

    async def test_cancellation_is_not_mistaken_for_an_outage(self) -> None:
        # A caller walking away must not be recorded as a settings failure,
        # and must not leave the enclosing TaskGroup believing the read
        # completed.
        resolver = mock_of[ConfigResolver](
            get_provider_configs=AsyncMock(side_effect=asyncio.CancelledError())
        )

        with pytest.raises(asyncio.CancelledError):
            await providers_for_capabilities(_app_state(resolver))
