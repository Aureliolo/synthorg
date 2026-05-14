"""Tests for the registry-based escalation factory dispatch.

The factory consults a frozen registry map keyed by the config
discriminator rather than a hardcoded if/elif chain. These tests
verify each registered branch builds the expected store and that an
unregistered key raises ValueError with a helpful message listing the
available options.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
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
    WinnerOnlyDecisionProcessor,
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

    @pytest.mark.parametrize(
        ("cross_instance_notify", "configured_channel", "expected_kwarg"),
        [
            # ``on`` + explicit channel: the channel propagates to
            # ``build_escalations`` so the Postgres LISTEN/NOTIFY surface
            # subscribes to the right name.
            ("on", "escalations", "escalations"),
            # ``off`` collapses to ``notify_channel=None`` so the store
            # does not bind a Postgres NOTIFY channel even if one is
            # configured for some other component.
            ("off", None, None),
            # ``auto`` shares the ``on`` factory branch; the explicit
            # parametrize entry catches regressions that narrow the
            # equality back to ``== "on"`` and drop ``auto``.
            ("auto", "escalations", "escalations"),
        ],
        ids=("on", "off", "auto"),
    )
    def test_postgres_backend_passes_correct_notify_channel(
        self,
        cross_instance_notify: str,
        configured_channel: str | None,
        expected_kwarg: str | None,
    ) -> None:
        config_kwargs: dict[str, object] = {
            "backend": "postgres",
            "cross_instance_notify": cross_instance_notify,
        }
        if configured_channel is not None:
            config_kwargs["notify_channel"] = configured_channel
        config = EscalationQueueConfig(**config_kwargs)  # type: ignore[arg-type]
        backend = _fake_persistence("postgres")

        build_escalation_queue_store(config, backend)

        backend.build_escalations.assert_called_once_with(  # type: ignore[attr-defined]
            notify_channel=expected_kwarg,
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

    def test_in_memory_store_with_auto_returns_noop_subscriber(self) -> None:
        # InMemory has no capability marker; in ``auto`` mode the
        # factory must short-circuit to the no-op rather than raise,
        # so opportunistic notify configurations degrade gracefully.
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

    def test_in_memory_store_with_on_raises(self) -> None:
        # ``cross_instance_notify="on"`` is explicit operator intent;
        # silent degradation to a no-op subscriber would hide the
        # misconfiguration. The factory must raise ValueError so
        # startup fails fast with an actionable error.
        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="on",
            notify_channel="escalations",
        )
        store = InMemoryEscalationStore()
        registry = PendingFuturesRegistry()
        with pytest.raises(
            ValueError,
            match="CrossInstanceNotifyCapableStore",
        ):
            build_escalation_notify_subscriber(
                config,
                store,
                registry,
                reconnect_delay_seconds=1.0,
            )

    def test_non_postgres_backend_with_auto_returns_noop(self) -> None:
        # ``cross_instance_notify="auto"`` plus a non-Postgres backend
        # must return the no-op subscriber without consulting the
        # store's capability surface. Closes the boundary the original
        # capability check was added to enforce.
        config = EscalationQueueConfig(
            backend="sqlite",
            cross_instance_notify="auto",
        )
        store = InMemoryEscalationStore()
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
        # AND exposes a working ``subscribe_notifications`` async
        # context manager passes the structural + capability + API
        # gate and gets a real subscriber. The factory's runtime check
        # rejects fakes that flip the flag but never wire the
        # LISTEN/NOTIFY surface; see
        # ``test_capable_store_without_subscribe_method_is_rejected``.
        # The fake's CM yields no payloads -- the subscriber is
        # constructed but ``start()`` would observe an immediate
        # iterator exit, matching the no-cross-instance-traffic shape
        # operators see when there are no concurrent workers.
        @asynccontextmanager
        async def _empty_subscription(
            channel: str,
        ) -> AsyncIterator[AsyncIterator[str]]:
            del channel

            async def _gen() -> AsyncIterator[str]:
                # Empty subscription -- yields nothing, mirroring the
                # no-cross-instance-traffic shape. The ``yield`` is
                # gated by a name lookup so mypy / ruff cannot prune
                # it as structurally unreachable, and the conditional
                # always evaluates false at runtime, so the iterator
                # exits immediately with no payload.
                emit = False
                if emit:  # pragma: no cover
                    yield ""

            yield _gen()

        class _CapableFakeStore:
            supports_cross_instance_notify = True

            def subscribe_notifications(
                self,
                channel: str,
            ) -> AbstractAsyncContextManager[AsyncIterator[str]]:
                return _empty_subscription(channel)

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

    def test_capable_store_without_subscribe_method_is_rejected(self) -> None:
        # A fake that flips the capability marker to ``True`` but does
        # not wire ``subscribe_notifications`` would otherwise reach the
        # real subscriber and fail at first iteration. The factory
        # closes that gap by also requiring the callable, raising at
        # configuration time on ``mode="on"``.
        class _MarkerOnlyStore:
            supports_cross_instance_notify = True

        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="on",
            notify_channel="escalations",
        )
        store = cast(EscalationQueueStore, _MarkerOnlyStore())
        registry = PendingFuturesRegistry()
        with pytest.raises(ValueError, match="subscribe_notifications"):
            build_escalation_notify_subscriber(
                config,
                store,
                registry,
                reconnect_delay_seconds=1.0,
            )

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

    def test_explicit_false_capability_with_auto_returns_noop(self) -> None:
        # A store that explicitly sets ``supports_cross_instance_notify
        # = False`` (rather than omitting the attribute) is structurally
        # distinct from a marker-missing store; the capability gate
        # still degrades to the no-op when the config is in ``auto``
        # mode. Closes the gap between "no attribute" and "attribute
        # present but explicitly False".
        class _NotCapableFakeStore:
            supports_cross_instance_notify = False

        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="auto",
            notify_channel="escalations",
        )
        store = cast(EscalationQueueStore, _NotCapableFakeStore())
        registry = PendingFuturesRegistry()
        subscriber = build_escalation_notify_subscriber(
            config,
            store,
            registry,
            reconnect_delay_seconds=1.0,
        )
        assert isinstance(subscriber, NoopEscalationNotifySubscriber)

    def test_explicit_false_capability_with_on_raises(self) -> None:
        # ``cross_instance_notify="on"`` plus a store whose capability
        # marker is explicitly ``False`` is operator intent that cannot
        # be satisfied: the factory must raise so misconfiguration is
        # surfaced at startup rather than silently degraded.
        class _NotCapableFakeStore:
            supports_cross_instance_notify = False

        config = EscalationQueueConfig(
            backend="postgres",
            cross_instance_notify="on",
            notify_channel="escalations",
        )
        store = cast(EscalationQueueStore, _NotCapableFakeStore())
        registry = PendingFuturesRegistry()
        with pytest.raises(
            ValueError,
            match="CrossInstanceNotifyCapableStore",
        ):
            build_escalation_notify_subscriber(
                config,
                store,
                registry,
                reconnect_delay_seconds=1.0,
            )


class TestDecisionProcessorRegistry:
    """``build_decision_processor`` dispatches via the registry map."""

    def test_winner_strategy_returns_winner_only_processor(self) -> None:
        config = EscalationQueueConfig(decision_strategy="winner")
        processor = build_decision_processor(config)
        assert isinstance(processor, WinnerOnlyDecisionProcessor)

    def test_hybrid_strategy_returns_hybrid_processor(self) -> None:
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
