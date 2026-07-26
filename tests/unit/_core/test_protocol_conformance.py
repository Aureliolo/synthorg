"""Conformance tests for the collaborator Protocols.

Each ``@runtime_checkable`` collaborator Protocol must be satisfied by both the
concrete collaborator and the canonical ``mock_of`` autospec double, so a
consumer can annotate against the Protocol and have the real object and the
injected double both pass an ``isinstance`` boundary check. These tests pin
that: the real class satisfies the Protocol structurally, the autospec double
satisfies it via ``isinstance``, the behavioural ``FakeJetStreamTaskQueue``
double satisfies ``TaskQueue``, and a bare object never conforms (so the
decorator is actually enforcing membership). A ``TYPE_CHECKING``-only block adds
the static guarantee that each concrete class is assignable to its Protocol.
"""

import typing
from typing import TYPE_CHECKING

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.security.risk_map import MapBackedRiskClassifier
from synthorg.security.timeout.protocol import RiskTierClassifier
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.service import SettingsService
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.workers.claim import JetStreamTaskQueue
from synthorg.workers.queue_protocol import TaskQueue
from tests._shared import mock_of
from tests._shared.fake_task_queue import FakeJetStreamTaskQueue

pytestmark = pytest.mark.unit

# (Protocol, concrete collaborator) pairs. The concrete class is the production
# implementation the Protocol was extracted from; it must remain a structural
# superset so consumers can hold it by the Protocol.
_PAIRS: tuple[tuple[type, type], ...] = (
    (TaskQueue, JetStreamTaskQueue),
    (ParallelExecutorProtocol, ParallelExecutor),
    (ConfigResolverProtocol, ConfigResolver),
    (SettingsServiceProtocol, SettingsService),
    (CostTrackerProtocol, CostTracker),
    (AgentRegistryProtocol, AgentRegistryService),
    (RiskTierClassifier, MapBackedRiskClassifier),
)


def _missing_members(proto: type, candidate: object) -> set[str]:
    """Protocol members absent from *candidate* (the runtime-checkable check)."""
    return {
        member
        for member in typing.get_protocol_members(proto)
        if not hasattr(candidate, member)
    }


@pytest.mark.parametrize("pair", _PAIRS, ids=[p[0].__name__ for p in _PAIRS])
def test_real_class_satisfies_protocol(pair: tuple[type, type]) -> None:
    """The concrete collaborator structurally satisfies its Protocol."""
    proto, real_cls = pair
    missing = _missing_members(proto, real_cls)
    assert not missing, (
        f"{real_cls.__name__} is missing {missing} required by {proto.__name__}"
    )


@pytest.mark.parametrize("pair", _PAIRS, ids=[p[0].__name__ for p in _PAIRS])
def test_autospec_double_satisfies_protocol(pair: tuple[type, type]) -> None:
    """The canonical ``mock_of`` autospec double satisfies the Protocol.

    ``create_autospec`` mirrors the spec class without instantiating it, so the
    double conforms to any Protocol the real class satisfies and isinstance
    passes at the boundary an annotated consumer would enforce.
    """
    proto, real_cls = pair
    double = mock_of[real_cls]()
    assert isinstance(double, proto)


def test_fake_task_queue_satisfies_protocol() -> None:
    """The in-memory distributed-path double satisfies ``TaskQueue``."""
    assert isinstance(FakeJetStreamTaskQueue(), TaskQueue)


@pytest.mark.parametrize("pair", _PAIRS, ids=[p[0].__name__ for p in _PAIRS])
def test_bare_object_does_not_satisfy_protocol(pair: tuple[type, type]) -> None:
    """A bare object never conforms, so the decorator enforces membership."""
    proto, _ = pair
    assert not isinstance(object(), proto)


if TYPE_CHECKING:

    def _accepts_protocols(  # noqa: PLR0913 -- one param per collaborator Protocol under test
        queue: TaskQueue,
        executor: ParallelExecutorProtocol,
        resolver: ConfigResolverProtocol,
        settings: SettingsServiceProtocol,
        tracker: CostTrackerProtocol,
        registry: AgentRegistryProtocol,
        classifier: RiskTierClassifier,
    ) -> None: ...

    def _real_classes_are_assignable_to_protocols(  # noqa: PLR0913 -- mirrors _accepts_protocols
        queue: JetStreamTaskQueue,
        executor: ParallelExecutor,
        resolver: ConfigResolver,
        settings: SettingsService,
        tracker: CostTracker,
        registry: AgentRegistryService,
        classifier: MapBackedRiskClassifier,
    ) -> None:
        """Static-only: mypy proves each concrete class is assignable to its Protocol.

        Verifies signature compatibility (parameter and return types, async-ness)
        that the runtime ``isinstance`` member-presence check cannot. Never called
        at runtime.
        """
        _accepts_protocols(
            queue,
            executor,
            resolver,
            settings,
            tracker,
            registry,
            classifier,
        )
