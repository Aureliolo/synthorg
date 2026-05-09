"""Tests for :mod:`synthorg.communication.conflict_resolution.escalation.notify`.

Focuses on lifecycle branches added in the #1683 reliability bundle:

* The ``_stop_failed`` unrestartable guard refuses a fresh ``start()``
  after a ``stop()`` drain that exceeded the hard deadline.
* The hard-deadline drain raises ``TimeoutError`` instead of blocking
  ``stop()`` indefinitely on a callee that suppresses
  ``CancelledError``.

The Postgres LISTEN/NOTIFY plumbing itself is exercised in the
persistence-layer integration suite; here we patch the run loop to
isolate the lifecycle behaviour.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.conflict_resolution.escalation import notify as notify_mod
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.notify import (
    NoopEscalationNotifySubscriber,
    PostgresEscalationNotifySubscriber,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
    CONFLICT_ESCALATION_SUBSCRIBER_PAUSED,
)
from synthorg.settings.resolver import ConfigResolver

pytestmark = pytest.mark.unit


class TestNoopSubscriber:
    async def test_start_stop_are_noops(self) -> None:
        """The Noop variant exists for SQLite/in-memory deployments."""
        subscriber = NoopEscalationNotifySubscriber()
        await subscriber.start()
        await subscriber.stop()

    async def test_set_config_resolver_is_noop(self) -> None:
        """The Noop subscriber accepts the rebind without effect."""
        subscriber = NoopEscalationNotifySubscriber()
        resolver = AsyncMock(spec=ConfigResolver)
        subscriber.set_config_resolver(resolver)


class TestPostgresSubscriberLateBoundResolver:
    """Late-binding the resolver after construction enables runtime gating.

    On the auto-wire startup path ``app_state.config_resolver`` is not
    available when the subscriber is built, so the constructor captures
    ``None``. The lifecycle hook then calls ``set_config_resolver`` once
    the resolver is wired -- after which the loop body's
    ``communication.escalation_notify_subscriber_enabled`` reads honour
    the live operator-tuned value.
    """

    async def test_set_config_resolver_replaces_eager_none(self) -> None:
        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        subscriber = PostgresEscalationNotifySubscriber(
            repo,
            registry,
            channel="escalations",
            reconnect_delay_seconds=1.0,
        )
        # Asserting the eager ``None`` capture would narrow the
        # attribute type to ``None`` for the rest of the function, so
        # mypy would flag the post-rebind ``is resolver`` assertion as
        # unreachable. Read into a local instead.
        eager_resolver: ConfigResolver | None = subscriber._config_resolver
        assert eager_resolver is None

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        subscriber.set_config_resolver(resolver)
        rebound_resolver: ConfigResolver | None = subscriber._config_resolver
        assert rebound_resolver is resolver
        # The kill-switch helper now consults the live resolver. Pinning
        # the namespace + key here turns this from a smoke test into a
        # regression test against ``communication.escalation_notify_subscriber_enabled``
        # drift -- if a future refactor passes the wrong setting key,
        # the assertion fails with the exact mismatch instead of
        # silently passing on any awaited call.
        assert (await subscriber._resolve_subscriber_enabled()) is False
        resolver.get_bool.assert_awaited_once_with(
            "communication",
            "escalation_notify_subscriber_enabled",
        )

    async def test_run_loop_paused_branch_uses_paused_event(self) -> None:
        """When the kill-switch is False the debug log uses the PAUSED event."""
        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        resolver = AsyncMock(spec=ConfigResolver)
        gate_consulted = asyncio.Event()

        async def _gate(*_a: object, **_k: object) -> bool:
            gate_consulted.set()
            return False

        resolver.get_bool.side_effect = _gate
        subscriber = PostgresEscalationNotifySubscriber(
            repo,
            registry,
            channel="escalations",
            reconnect_delay_seconds=1.0,
            config_resolver=resolver,
        )
        subscriber._reconnect_delay = 0.01

        debug_events: list[str] = []
        proxy = notify_mod.logger
        original_debug = proxy.debug

        def _spy(event: str, **kwargs: object) -> None:
            debug_events.append(event)
            original_debug(event, **kwargs)

        # ``BoundLoggerLazyProxy`` serves ``debug`` via ``__getattr__``
        # (no instance dict entry until we set one). Direct assignment
        # plus ``del`` in the finally block lets ``__getattr__`` resume
        # serving fresh bound loggers for the next test, matching the
        # canonical ``_logger_info_spy`` pattern in tests/unit/settings.
        proxy.debug = _spy  # type: ignore[method-assign,assignment]
        try:
            task = asyncio.create_task(subscriber._run())
            try:
                await asyncio.wait_for(gate_consulted.wait(), timeout=1.0)
            finally:
                subscriber._stop_event.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1.0)
        finally:
            with contextlib.suppress(AttributeError):
                del proxy.debug

        assert CONFLICT_ESCALATION_SUBSCRIBER_PAUSED in debug_events, (
            "PAUSED event must fire on operator-controlled pause"
        )
        assert CONFLICT_ESCALATION_SUBSCRIBER_FAILED not in debug_events, (
            "FAILED must not fire for the paused-by-setting path"
        )


class TestPostgresSubscriberValidation:
    async def test_invalid_channel_rejected(self) -> None:
        """Defence-in-depth: hand-constructed unsafe channel raises."""
        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        # The constructor must REJECT this literal, so it has to appear
        # verbatim on the channel= argument; the trailing marker keeps
        # the persistence-boundary gate quiet.
        bad_channel = "bad; DROP TABLE"  # lint-allow: persistence-boundary -- negative-path literal asserted REJECTED  # noqa: E501
        with pytest.raises(ValueError, match="not a safe Postgres identifier"):
            PostgresEscalationNotifySubscriber(
                repo,
                registry,
                channel=bad_channel,
                reconnect_delay_seconds=1.0,
            )

    async def test_negative_reconnect_delay_rejected(self) -> None:
        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        with pytest.raises(ValueError, match="reconnect_delay_seconds"):
            PostgresEscalationNotifySubscriber(
                repo,
                registry,
                channel="escalations",
                reconnect_delay_seconds=0,
            )


class TestPostgresSubscriberLifecycle:
    async def test_stop_drain_timeout_marks_unrestartable(self) -> None:
        """A ``stop()`` drain that exceeds the hard deadline marks the
        subscriber unrestartable so a subsequent ``start()`` cannot
        spawn a fresh task while the orphan loop still holds the
        LISTEN connection.
        """
        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        subscriber = PostgresEscalationNotifySubscriber(
            repo,
            registry,
            channel="escalations",
            reconnect_delay_seconds=1.0,
        )
        await subscriber.start()
        original_task = subscriber._task
        assert original_task is not None
        # Cancel + await the real ``_run`` so we do not leak it.
        original_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await original_task

        # Replace ``_task`` with a coroutine that swallows cancellation
        # using the ``uncancel`` recipe (Python 3.11+). The release
        # event lets the test reliably reap the orphan during
        # teardown so xdist does not see a pending task at exit.
        release_event = asyncio.Event()

        async def _stuck() -> None:
            never = asyncio.get_event_loop().create_future()
            while not release_event.is_set():
                try:
                    await asyncio.wait(
                        [never, asyncio.create_task(release_event.wait())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None:
                        current.uncancel()
                    continue

        stuck_task = asyncio.create_task(_stuck(), name="stuck-notify")
        subscriber._task = stuck_task
        subscriber._stop_drain_timeout_seconds = 0.05
        # Yield once so ``_stuck`` reaches its first suspend point;
        # without this asyncio satisfies the cancel by simply not
        # starting the coroutine.
        await asyncio.sleep(0)

        with pytest.raises(TimeoutError):
            await subscriber.stop()
        assert subscriber._stop_failed is True

        # A subsequent start() must refuse to spawn a fresh task.
        with pytest.raises(RuntimeError, match="unrestartable"):
            await subscriber.start()

        # Tear down the orphan stuck task so xdist does not inherit
        # the leaked future.
        release_event.set()
        try:
            await asyncio.wait_for(stuck_task, timeout=1.0)
        except asyncio.CancelledError, TimeoutError:
            stuck_task.cancel()


class TestPostgresSubscriberDoneCallback:
    """``start()`` registers ``log_task_exceptions`` so MemoryError surfaces."""

    async def test_start_registers_log_task_exceptions(self) -> None:
        from unittest.mock import patch

        repo = AsyncMock(spec=InMemoryEscalationStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        subscriber = PostgresEscalationNotifySubscriber(
            repo,
            registry,
            channel="escalations",
            reconnect_delay_seconds=1.0,
        )

        # ``add_done_callback`` requires a plain sync callable; a
        # function sentinel keeps the gate-blocking bare-Mock pattern
        # out of test code while still letting us assert the factory
        # was invoked with the expected kwargs.
        def _sentinel(_task: asyncio.Task[None]) -> None:
            return

        with patch(
            "synthorg.communication.conflict_resolution.escalation.notify.log_task_exceptions",
            return_value=_sentinel,
        ) as patched:
            try:
                await subscriber.start()
                assert patched.called
                args = patched.call_args.args
                kwargs = patched.call_args.kwargs
                assert args[0] is notify_mod.logger
                assert args[1] == CONFLICT_ESCALATION_SUBSCRIBER_FAILED
                assert kwargs.get("channel") == "escalations"
            finally:
                # Reach in and cancel the real ``_run`` we just started
                # so xdist does not inherit a pending task.
                task = subscriber._task
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
