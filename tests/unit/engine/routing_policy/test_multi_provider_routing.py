"""Per-task multi-provider routing: attribution parity (WS-D5).

Stakes routing over multiple providers can pick a model owned by a provider
other than the engine default. The engine then swaps the dispatched client to
that provider so the API actually called and the cost attribution
(``identity.model.provider``) name the same provider. These tests pin that
parity and the fail-safe when a routed provider cannot be resolved.
"""

import pytest

from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskType
from synthorg.core.types import ModelTier
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.routing_policy import StakesRoutingConfig, build_stakes_router
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.providers.routing.selector import CheapestSelector
from tests._shared import as_uuid, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

_DEFAULT_PROVIDER = "default-provider"
_CHEAP_PROVIDER = "cheap-provider"


def _multi_provider_resolver() -> ModelResolver:
    """A resolver where the ``large`` tier is served by two providers.

    ``cheap-provider`` is cheaper, so ``CheapestSelector`` picks it over the
    default provider for the ``large`` tier.
    """
    large_default = ResolvedModel(
        provider_name=_DEFAULT_PROVIDER,
        model_id="default-large-001",
        alias="large",
        cost_per_1k_input=2.0,
        cost_per_1k_output=2.0,
        max_context=128000,
        estimated_latency_ms=100,
    )
    large_cheap = ResolvedModel(
        provider_name=_CHEAP_PROVIDER,
        model_id="cheap-large-001",
        alias="large",
        cost_per_1k_input=0.5,
        cost_per_1k_output=0.5,
        max_context=128000,
        estimated_latency_ms=100,
    )
    small_default = ResolvedModel(
        provider_name=_DEFAULT_PROVIDER,
        model_id="default-small-001",
        alias="small",
        cost_per_1k_input=0.1,
        cost_per_1k_output=0.1,
        max_context=128000,
        estimated_latency_ms=100,
    )
    return ModelResolver(
        {
            "large": (large_default, large_cheap),
            "small": (small_default,),
        },
        selector=CheapestSelector(),
    )


def _identity(*, provider: str, model_id: str, tier: ModelTier) -> AgentIdentity:
    return make_e2e_identity().model_copy(
        update={
            "model": ModelConfig(
                provider=provider,
                model_id=model_id,
                model_tier=tier,
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
    router = build_stakes_router(
        StakesRoutingConfig(),
        benchmark_provider=StubBenchmarkScoreProvider(),
        resolver=_multi_provider_resolver(),
    )
    return AgentEngine(
        provider=default_provider,
        provider_registry=registry,
        stakes_router=router,
    )


@pytest.mark.unit
class TestMultiProviderAttributionParity:
    """The dispatched instance and the attributed provider stay identical."""

    async def test_high_stakes_routes_to_cheaper_provider_with_parity(self) -> None:
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
            provider=_DEFAULT_PROVIDER, model_id="default-small-001", tier="small"
        )
        routed = await engine._route_stakes(identity, _task(Stakes.HIGH))
        provider, final_identity = engine._resolve_provider_instance(
            routed, identity, default_client
        )

        # Routing upgraded to the large tier served cheapest by cheap-provider.
        assert final_identity.model.provider == _CHEAP_PROVIDER
        assert final_identity.model.model_tier == "large"
        # The dispatched client is cheap-provider's, matching the attribution.
        assert provider is cheap_client

    async def test_same_provider_route_keeps_default_instance(self) -> None:
        default_client = ScriptedProvider([])

        # A registry whose ``get`` must never be consulted on the same-provider
        # path: reaching it would be a wasteful lookup for an unchanged client.
        def _boom(_name: str) -> CompletionProvider:
            msg = "registry.get must not be called for a same-provider route"
            raise AssertionError(msg)

        registry = mock_of[ProviderRegistry](get=_boom)
        engine = _engine(default_provider=default_client, registry=registry)

        # Routed to a different model but the SAME (default) provider.
        routed = _identity(
            provider=_DEFAULT_PROVIDER, model_id="default-large-001", tier="large"
        )
        prior = _identity(
            provider=_DEFAULT_PROVIDER, model_id="default-small-001", tier="small"
        )
        provider, final_identity = engine._resolve_provider_instance(
            routed, prior, default_client
        )
        assert final_identity.model.provider == _DEFAULT_PROVIDER
        assert final_identity.model.model_id == "default-large-001"
        # Same provider -> the default instance is reused, not re-fetched.
        assert provider is default_client

    async def test_unresolvable_routed_provider_falls_back_to_default(self) -> None:
        default_client = ScriptedProvider([])

        def _get(name: str) -> CompletionProvider:
            raise DriverNotRegisteredError(name, context={"name": name})

        registry = mock_of[ProviderRegistry](get=_get)
        engine = _engine(default_provider=default_client, registry=registry)

        # A routed identity naming a provider the registry does not know.
        routed = _identity(
            provider="ghost-provider", model_id="ghost-large-001", tier="large"
        )
        prior = _identity(
            provider=_DEFAULT_PROVIDER, model_id="default-small-001", tier="small"
        )
        provider, final_identity = engine._resolve_provider_instance(
            routed, prior, default_client
        )
        # Parity preserved: keep the prior identity + default client together.
        assert final_identity.model.provider == _DEFAULT_PROVIDER
        assert provider is default_client


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
            provider=_CHEAP_PROVIDER, model_id="cheap-large-001", tier="large"
        )
        assert engine._dispatch_client_for(identity, default_client) is cheap_client

    def test_dispatch_no_registry_falls_back_to_default(self) -> None:
        default_client = ScriptedProvider([])
        engine = _engine(default_provider=default_client, registry=None)

        identity = _identity(
            provider=_CHEAP_PROVIDER, model_id="cheap-large-001", tier="large"
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
            provider="ghost-provider", model_id="ghost-large-001", tier="large"
        )
        with pytest.raises(DriverNotRegisteredError):
            engine._dispatch_client_for(identity, default_client)

    async def test_noop_route_on_nondefault_provider_dispatches_to_agent(self) -> None:
        """The exact failing scenario: agent on a non-default provider whose
        stakes routing cannot resolve a tier (no-op) still runs on its own
        provider, not the engine default.
        """
        default_client, cheap_client, registry = self._clients()
        # A router whose resolver yields no tiers reproduces the observed
        # ``no_tier_resolved`` no-op that kept the agent's model unchanged.
        engine = AgentEngine(
            provider=default_client,
            provider_registry=registry,
            stakes_router=build_stakes_router(
                StakesRoutingConfig(),
                benchmark_provider=StubBenchmarkScoreProvider(),
                resolver=None,
            ),
        )

        identity = _identity(
            provider=_CHEAP_PROVIDER, model_id="cheap-large-001", tier="large"
        )
        dispatched = engine._dispatch_client_for(identity, default_client)
        routed = await engine._route_stakes(identity, _task(Stakes.HIGH))
        resolved, final_identity = engine._resolve_provider_instance(
            routed, identity, dispatched
        )

        assert routed.model == identity.model  # routing was a no-op
        assert final_identity.model.provider == _CHEAP_PROVIDER
        assert resolved is cheap_client  # dispatched to the agent's provider
