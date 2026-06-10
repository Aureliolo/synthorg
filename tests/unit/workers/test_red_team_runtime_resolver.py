"""Unit tests for the production grounding-substrate resolver closure.

Every other substrate test injects a hand-built ``lambda: context``; this
file exercises the real closure built in the worker boot path, which reads
the live application state slices each time the checker calls it. It is the
sole bridge from configuration to the substrate checker, so its two
contracts are pinned here: a context when a provider is registered (picking
up the live knowledge service and cost tracker), and ``None`` when the
provider registry is empty or absent so the checker degrades to heuristic.
"""

from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.budget.state import BudgetStateSlice
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext
from synthorg.workers._red_team_runtime import (
    _GROUNDING_MODEL_ID,
    _build_grounding_substrate_resolver,
)
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


class _FakeAppState:
    """Duck-typed ``AppState`` exposing only ``slice`` for the three slices."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None,
        service: KnowledgeService | None,
        cost_tracker: object,
    ) -> None:
        self._slices: dict[type, SimpleNamespace] = {
            ProvidersStateSlice: SimpleNamespace(registry=registry),
            KnowledgeStateSlice: SimpleNamespace(service=service),
            BudgetStateSlice: SimpleNamespace(cost_tracker=cost_tracker),
        }

    def slice(self, slice_type: type) -> SimpleNamespace:
        return self._slices[slice_type]


def _provider() -> BaseCompletionProvider:
    return cast("BaseCompletionProvider", mock_of[BaseCompletionProvider]())


def test_resolver_returns_context_when_provider_registered() -> None:
    provider = _provider()
    registry = ProviderRegistry({"example-provider": provider})
    knowledge = mock_of[KnowledgeService]()
    app_state = _FakeAppState(
        registry=registry,
        service=knowledge,
        cost_tracker=None,
    )

    resolve = _build_grounding_substrate_resolver(
        app_state,  # type: ignore[arg-type]
        provider_name="example-provider",
    )
    context = resolve()

    assert isinstance(context, GroundingSubstrateContext)
    assert context.knowledge_service is knowledge
    assert context.provider is provider
    assert context.model_id == _GROUNDING_MODEL_ID


def test_resolver_picks_up_late_wired_knowledge_service() -> None:
    # The closure reads state live, so a service wired AFTER the resolver is
    # built (the real boot ordering) is still resolved.
    registry = ProviderRegistry({"example-provider": _provider()})
    app_state = _FakeAppState(registry=registry, service=None, cost_tracker=None)
    resolve = _build_grounding_substrate_resolver(
        app_state,  # type: ignore[arg-type]
        provider_name="example-provider",
    )

    assert resolve() is not None
    assert resolve().knowledge_service is None  # type: ignore[union-attr]

    knowledge = mock_of[KnowledgeService]()
    app_state._slices[KnowledgeStateSlice] = SimpleNamespace(service=knowledge)

    assert resolve().knowledge_service is knowledge  # type: ignore[union-attr]


def test_resolver_falls_back_to_first_provider_when_name_absent() -> None:
    provider = _provider()
    registry = ProviderRegistry({"only-provider": provider})
    app_state = _FakeAppState(registry=registry, service=None, cost_tracker=None)

    resolve = _build_grounding_substrate_resolver(
        app_state,  # type: ignore[arg-type]
        provider_name="missing-provider",
    )
    context = resolve()

    assert context is not None
    assert context.provider is provider


def test_resolver_returns_none_when_registry_empty() -> None:
    app_state = _FakeAppState(
        registry=ProviderRegistry({}),
        service=mock_of[KnowledgeService](),
        cost_tracker=None,
    )

    resolve = _build_grounding_substrate_resolver(
        app_state,  # type: ignore[arg-type]
        provider_name="example-provider",
    )

    assert resolve() is None


def test_resolver_returns_none_when_registry_absent() -> None:
    app_state = _FakeAppState(registry=None, service=None, cost_tracker=None)

    resolve = _build_grounding_substrate_resolver(
        app_state,  # type: ignore[arg-type]
        provider_name="example-provider",
    )

    assert resolve() is None
