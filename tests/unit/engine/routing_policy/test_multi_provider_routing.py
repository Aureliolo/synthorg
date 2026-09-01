"""Per-task multi-provider dispatch: attribution parity.

An agent binds an exclusive ``(provider, model)`` pair and the stakes gate
never moves it, so the API actually called and the cost attribution
(``identity.model.provider``) name the same provider by construction. These
tests pin that the gate leaves the pair alone even where a cheaper provider
serves an equivalent model, and that run-start dispatch resolves the agent's
own provider rather than the engine default.
"""

from dataclasses import replace

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import CapabilityLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.routing_policy import (
    CapabilityPolicy,
    CapabilityPolicyConfig,
    ResolvedAgentCapabilityReader,
)
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.providers.routing.selector import CheapestSelector
from tests._shared import UNWIRED_ORG, UNWIRED_ROUTING, as_uuid, engine_with, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

_DEFAULT_PROVIDER = "default-provider"
_CHEAP_PROVIDER = "cheap-provider"


def _multi_provider_resolver() -> ModelResolver:
    """A resolver where the ``expert`` rung is served by two providers.

    ``cheap-provider`` is cheaper, so a cost-ordered selector would prefer it.
    Nothing in the gate consults that ordering any more, which is the point:
    the agent's own pair decides.
    """
    large_default = ResolvedModel(
        provider_name=_DEFAULT_PROVIDER,
        model_id="default-expert-001",
        alias="expert",
        cost_per_1k_input=2.0,
        cost_per_1k_output=2.0,
        max_context=128000,
        estimated_latency_ms=100,
        capability="expert",
    )
    large_cheap = ResolvedModel(
        provider_name=_CHEAP_PROVIDER,
        model_id="cheap-expert-001",
        alias="expert",
        cost_per_1k_input=0.5,
        cost_per_1k_output=0.5,
        max_context=128000,
        estimated_latency_ms=100,
        capability="expert",
    )
    small_default = ResolvedModel(
        provider_name=_DEFAULT_PROVIDER,
        model_id="default-basic-001",
        alias="basic",
        cost_per_1k_input=0.1,
        cost_per_1k_output=0.1,
        max_context=128000,
        estimated_latency_ms=100,
        capability="basic",
    )
    return ModelResolver(
        {
            "expert": (large_default, large_cheap),
            "basic": (small_default,),
            "default-expert-001": (large_default,),
            "cheap-expert-001": (large_cheap,),
            "default-basic-001": (small_default,),
        },
        selector=CheapestSelector(),
    )


def _identity(
    *,
    provider: str,
    model_id: str,
    capability: CapabilityLevel,
) -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=provider,
                model_id=model_id,
                capability=capability,
            ),
        },
    )


def _task(stakes: Stakes) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="A task",
        description="Body",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
        stakes=stakes,
    )


def _engine(
    *,
    default_provider: CompletionProvider,
    registry: ProviderRegistry | None,
) -> AgentEngine:
    return engine_with(
        default_provider,
        routing=replace(UNWIRED_ROUTING, provider_registry=registry),
        org=replace(
            UNWIRED_ORG,
            capability=CapabilityPolicy(
                config=CapabilityPolicyConfig(),
                reader=ResolvedAgentCapabilityReader(_multi_provider_resolver()),
            ),
        ),
    )


@pytest.mark.unit
class TestTheGateNeverMovesAnAgentAcrossProviders:
    """A cheaper equivalent elsewhere is not a reason to move an agent."""

    def test_a_cleared_agent_keeps_its_expensive_provider(self) -> None:
        """The saving is not the gate's to take.

        ``cheap-provider`` serves an equally-capable model for a quarter of
        the price, and the gate leaves the agent on the pair the operator
        bound it to anyway: moving it would spread one agent's run history
        across two connections billed and rate-limited separately.
        """
        default_client = ScriptedProvider([])
        engine = _engine(default_provider=default_client, registry=None)
        identity = _identity(
            provider=_DEFAULT_PROVIDER,
            model_id="default-expert-001",
            capability="expert",
        )

        effort = engine._check_capability(identity, _task(Stakes.HIGH))

        # Assert on what the gate RETURNS. ``AgentIdentity`` is frozen, so
        # re-reading the argument afterwards holds whatever it held going in
        # and pins nothing whatever the implementation did. The whole of the
        # gate's output is the reasoning dial: it hands back no model, which
        # is what leaves no seam for a cheaper pair to arrive through.
        assert effort is ReasoningEffort.MEDIUM

    def test_dispatch_targets_the_agents_own_provider_after_the_gate(
        self,
    ) -> None:
        default_client = ScriptedProvider([])
        cheap_client = ScriptedProvider([])
        clients = {_DEFAULT_PROVIDER: default_client, _CHEAP_PROVIDER: cheap_client}

        def _get(name: str) -> CompletionProvider:
            if name not in clients:
                raise DriverNotRegisteredError(name, context={"name": name})
            return clients[name]

        registry = mock_of[ProviderRegistry](get=_get)
        engine = _engine(default_provider=default_client, registry=registry)
        identity = _identity(
            provider=_CHEAP_PROVIDER,
            model_id="cheap-expert-001",
            capability="expert",
        )

        engine._check_capability(identity, _task(Stakes.HIGH))

        assert engine._dispatch_client_for(identity, default_client) is cheap_client


@pytest.mark.unit
class TestDispatchClientResolution:
    """Run-start dispatch resolves the agent's OWN provider, not the default."""

    def _clients(
        self,
    ) -> tuple[CompletionProvider, CompletionProvider, ProviderRegistry]:
        default_client = ScriptedProvider([])
        cheap_client = ScriptedProvider([])
        clients = {_DEFAULT_PROVIDER: default_client, _CHEAP_PROVIDER: cheap_client}

        def _get(name: str) -> CompletionProvider:
            if name not in clients:
                raise DriverNotRegisteredError(name, context={"name": name})
            return clients[name]

        return default_client, cheap_client, mock_of[ProviderRegistry](get=_get)

    def test_dispatch_resolves_agent_provider_over_engine_default(self) -> None:
        """An agent pinned to a non-default provider dispatches to it.

        The engine holds the default provider's client, but the agent's model
        lives on another provider. Dispatching the default client would hit
        the wrong API (ModelNotFoundError) while cost attribution names the
        agent's provider, so dispatch must resolve the agent's own provider.
        """
        default_client, cheap_client, registry = self._clients()
        engine = _engine(default_provider=default_client, registry=registry)

        identity = _identity(
            provider=_CHEAP_PROVIDER, model_id="cheap-expert-001", capability="expert"
        )
        assert engine._dispatch_client_for(identity, default_client) is cheap_client

    def test_dispatch_no_registry_falls_back_to_default(self) -> None:
        default_client = ScriptedProvider([])
        engine = _engine(default_provider=default_client, registry=None)

        identity = _identity(
            provider=_CHEAP_PROVIDER, model_id="cheap-expert-001", capability="expert"
        )
        assert engine._dispatch_client_for(identity, default_client) is default_client

    def test_dispatch_unknown_provider_raises(self) -> None:
        # A wired registry that does not know the agent's provider must fail
        # closed: silently falling back to the default client would dispatch
        # to the wrong API and misattribute cost.
        default_client = ScriptedProvider([])

        def _get(name: str) -> CompletionProvider:
            raise DriverNotRegisteredError(name, context={"name": name})

        registry = mock_of[ProviderRegistry](get=_get)
        engine = _engine(default_provider=default_client, registry=registry)

        identity = _identity(
            provider="ghost-provider", model_id="ghost-expert-001", capability="expert"
        )
        with pytest.raises(DriverNotRegisteredError):
            engine._dispatch_client_for(identity, default_client)
