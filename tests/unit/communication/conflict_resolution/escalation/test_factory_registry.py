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
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.notify import (
    NoopEscalationNotifySubscriber,
    PostgresEscalationNotifySubscriber,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    HybridDecisionProcessor,
    WinnerSelectProcessor,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    CrossInstanceNotifyCapableStore,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.unit


def _fake_persistence(backend_name: str) -> PersistenceBackend:
    backend = MagicMock(spec=PersistenceBackend)
    backend.backend_name = backend_name
    # ``backend.build_escalations`` is auto-mocked by ``spec=`` to mirror
    # ``PersistenceBackend.build_escalations``; setting ``return_value``
    # avoids replacing the auto-mock with a bare MagicMock and keeps the
    # interface contract enforced.
    backend.build_escalations.return_value = MagicMock(spec=EscalationQueueStore)
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

    def test_postgres_backend_auto_passes_notify_channel(self) -> None:
        # ``auto`` and ``on`` share the same factory branch; covering
        # ``auto`` explicitly catches a regression where the equality
        # check is narrowed back to ``== "on"``.
        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="auto",
            notify_channel="escalations",
        )
        backend = _fake_persistence("postgres")
        build_escalation_queue_store(config, backend)
        backend.build_escalations.assert_called_once_with(  # type: ignore[attr-defined]
            notify_channel="escalations",
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


class TestNotifySubscriberCapabilityCheck:
    """``build_escalation_notify_subscriber`` uses the structural Protocol.

    The factory must not import the concrete
    ``PostgresEscalationRepository`` to decide whether to wire a real
    notify subscriber; it inspects the
    :class:`CrossInstanceNotifyCapableStore` capability marker
    instead. Stores opting in via the
    ``supports_cross_instance_notify`` attribute receive a real
    subscriber; everything else falls through to the no-op.
    """

    def test_in_memory_store_returns_noop_subscriber(self) -> None:
        # InMemory has no capability marker; the factory must short-
        # circuit to the no-op even when ``cross_instance_notify`` is
        # forced on (which would normally drive the Postgres path).
        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="auto",
            notify_channel="escalations",
        )
        store = InMemoryEscalationStore()
        assert not isinstance(store, CrossInstanceNotifyCapableStore)
        registry = PendingFuturesRegistry()
        subscriber = build_escalation_notify_subscriber(
            config,
            store,
            registry,
            reconnect_delay_seconds=1.0,
        )
        assert isinstance(subscriber, NoopEscalationNotifySubscriber)

    def test_capable_store_returns_real_subscriber(self) -> None:
        # A duck-typed store that declares the capability attribute
        # passes the structural check and gets a real subscriber.
        # No concrete persistence class is imported by the factory or
        # this test, and the real subscriber's __init__ never invokes
        # subscribe_notifications, so the fake needs no method body.
        class _CapableFakeStore:
            supports_cross_instance_notify = True

        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="on",
            notify_channel="escalations",
        )
        store = cast(EscalationQueueStore, _CapableFakeStore())
        assert isinstance(store, CrossInstanceNotifyCapableStore)
        registry = PendingFuturesRegistry()
        subscriber = build_escalation_notify_subscriber(
            config,
            store,
            registry,
            reconnect_delay_seconds=1.0,
        )
        assert isinstance(subscriber, PostgresEscalationNotifySubscriber)

    def test_off_returns_noop_regardless_of_capability(self) -> None:
        # Even a capability-marked store must yield the no-op when the
        # config says cross-instance notify is off.
        class _CapableFakeStore:
            supports_cross_instance_notify = True

        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="off",
        )
        store = cast(EscalationQueueStore, _CapableFakeStore())
        registry = PendingFuturesRegistry()
        subscriber = build_escalation_notify_subscriber(
            config,
            store,
            registry,
            reconnect_delay_seconds=1.0,
        )
        assert isinstance(subscriber, NoopEscalationNotifySubscriber)


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
        match = r"Unknown escalation queue backend"
        with pytest.raises(ValueError, match=match) as exc:
            build_escalation_queue_store(config)
        # Error message must enumerate the registered backends so a
        # caller hitting a typo learns the valid options without
        # spelunking the factory module.
        message = str(exc.value)
        for expected in ("memory", "sqlite", "postgres"):
            assert expected in message

    def test_unknown_decision_strategy_raises_value_error(self) -> None:
        config = EscalationQueueConfig.model_construct(decision_strategy="unknown")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=r"Unknown decision_strategy") as exc:
            build_decision_processor(config)
        message = str(exc.value)
        for expected in ("winner", "hybrid"):
            assert expected in message
