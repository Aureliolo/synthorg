"""Tests for the registry-based escalation factory dispatch.

The factory consults a frozen registry map keyed by the config
discriminator rather than a hardcoded if/elif chain. These tests
verify each registered branch builds the expected store and that an
unregistered key raises ValueError with a helpful message listing the
available options.
"""

from typing import cast
from unittest.mock import MagicMock

import pytest

from synthorg.communication.conflict_resolution.escalation.config import (
    EscalationQueueConfig,
)
from synthorg.communication.conflict_resolution.escalation.factory import (
    build_decision_processor,
    build_escalation_queue_store,
)
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    HybridDecisionProcessor,
    WinnerSelectProcessor,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.unit


def _fake_persistence(backend_name: str) -> PersistenceBackend:
    backend = MagicMock(spec=PersistenceBackend)
    backend.backend_name = backend_name
    backend.build_escalations = MagicMock(return_value=MagicMock())
    return cast(PersistenceBackend, backend)


class TestQueueStoreRegistry:
    """``build_escalation_queue_store`` dispatches via the registry map."""

    def test_memory_backend_returns_in_memory_store(self) -> None:
        config = EscalationQueueConfig(backend="memory")
        store = build_escalation_queue_store(config)
        assert isinstance(store, InMemoryEscalationStore)

    def test_sqlite_backend_calls_build_escalations(self) -> None:
        config = EscalationQueueConfig(backend="sqlite")
        backend = _fake_persistence("sqlite")
        build_escalation_queue_store(config, backend)
        backend.build_escalations.assert_called_once_with()  # type: ignore[attr-defined]

    def test_postgres_backend_passes_notify_channel_when_enabled(self) -> None:
        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="on",
            notify_channel="escalations",
        )
        backend = _fake_persistence("postgres")
        build_escalation_queue_store(config, backend)
        backend.build_escalations.assert_called_once_with(  # type: ignore[attr-defined]
            notify_channel="escalations",
        )

    def test_postgres_backend_off_passes_none_channel(self) -> None:
        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="off",
        )
        backend = _fake_persistence("postgres")
        build_escalation_queue_store(config, backend)
        backend.build_escalations.assert_called_once_with(  # type: ignore[attr-defined]
            notify_channel=None,
        )

    def test_sqlite_without_persistence_raises(self) -> None:
        config = EscalationQueueConfig(backend="sqlite")
        with pytest.raises(ValueError, match="connected persistence backend"):
            build_escalation_queue_store(config, persistence=None)

    def test_postgres_with_sqlite_persistence_raises(self) -> None:
        config = EscalationQueueConfig(backend="postgres")
        backend = _fake_persistence("sqlite")
        with pytest.raises(
            ValueError,
            match=r"config\.backend='postgres'",
        ):
            build_escalation_queue_store(config, backend)


class TestDecisionProcessorRegistry:
    """``build_decision_processor`` dispatches via the registry map."""

    def test_winner_strategy_returns_winner_select(self) -> None:
        config = EscalationQueueConfig(decision_strategy="winner")
        processor = build_decision_processor(config)
        assert isinstance(processor, WinnerSelectProcessor)

    def test_hybrid_strategy_returns_hybrid(self) -> None:
        config = EscalationQueueConfig(decision_strategy="hybrid")
        processor = build_decision_processor(config)
        assert isinstance(processor, HybridDecisionProcessor)


class TestRegistryFallback:
    """The defensive ValueError fallback fires for unregistered keys.

    Pydantic rejects unknown literals at config-construction time, so
    these tests bypass validation via ``model_construct`` to drive the
    factory directly and confirm that an unknown key surfaces a
    helpful error message rather than silently returning ``None`` or
    crashing inside the factory closure.
    """

    def test_unknown_queue_backend_raises_value_error(self) -> None:
        # Bypass Pydantic literal validation via ``model_construct`` so
        # the factory's defensive ValueError fires for the registered
        # unknown-key path.
        config = EscalationQueueConfig.model_construct(backend="unknown")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=r"Unknown escalation queue backend"):
            build_escalation_queue_store(config)

    def test_unknown_decision_strategy_raises_value_error(self) -> None:
        config = EscalationQueueConfig.model_construct(decision_strategy="unknown")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=r"Unknown decision_strategy"):
            build_decision_processor(config)
