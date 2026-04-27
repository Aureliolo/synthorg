"""Async-init contract for ``TelemetryCollector``.

Pins the resource-hygiene rule (#1600) that the collector's
``__init__`` must perform zero filesystem syscalls; the deployment ID
load is offloaded to a background thread inside ``start()``.

The bare-``open`` patches below intentionally trip on any builtin
``open`` call so the collector cannot accidentally cheat by routing
through ``Path.read_text`` or ``codecs.open``.
"""

import asyncio
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog.testing

from synthorg.observability.events.telemetry import (
    TELEMETRY_DEPLOYMENT_ID_CREATED,
    TELEMETRY_DEPLOYMENT_ID_LOADED,
    TELEMETRY_SHUTDOWN_WITHOUT_START,
)
from synthorg.telemetry.collector import TelemetryCollector
from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror the env-scrub fixture from ``test_collector.py``.

    Ensures the four-level env-resolution chain in
    :func:`_resolve_environment` cannot leak shell state into the
    async-init contract assertions.
    """
    monkeypatch.delenv("SYNTHORG_TELEMETRY", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV_BAKED", raising=False)
    for marker in ("CI", "GITLAB_CI", "BUILDKITE", "JENKINS_URL"):
        monkeypatch.delenv(marker, raising=False)
    for name in list(os.environ):
        if name.startswith("RUNPOD_"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
class TestConstructorIsPureConstruction:
    """``__init__`` must not touch the filesystem (#1600)."""

    @pytest.mark.parametrize(
        ("patch_target", "label"),
        [
            ("synthorg.telemetry.collector.os.open", "os.open"),
            ("synthorg.telemetry.collector.os.path.exists", "os.path.exists"),
            ("builtins.open", "builtin open()"),
        ],
    )
    def test_constructor_does_not_call_filesystem_syscall(
        self, tmp_path: Path, patch_target: str, label: str
    ) -> None:
        """Constructor performs zero filesystem syscalls (#1600).

        Patches the named syscall so any constructor-time call raises
        immediately. Each parameter exercises one syscall the prior
        synchronous ``__init__`` used: ``os.open`` (atomic create),
        ``os.path.exists`` (existence probe), ``builtins.open`` (the
        existing-file read). All three must stay on the
        ``asyncio.to_thread`` side of the lifecycle boundary in
        ``start()``.
        """

        def patched(*_args: object, **_kwargs: object) -> object:
            msg = f"{label} called from constructor"
            raise AssertionError(msg)

        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        with patch(patch_target, side_effect=patched):
            TelemetryCollector(config=config, data_dir=tmp_path)

    def test_constructor_leaves_deployment_id_unloaded(self, tmp_path: Path) -> None:
        """``deployment_id`` is ``None`` between ``__init__`` and ``start()``.

        Documents the new lifecycle contract: callers must
        ``await collector.start()`` before reading the property.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector.deployment_id is None


@pytest.mark.unit
class TestStartLoadsDeploymentIdAsynchronously:
    """``start()`` performs the load via ``asyncio.to_thread`` (#1600)."""

    async def test_start_populates_deployment_id(self, tmp_path: Path) -> None:
        """After ``start()``, ``deployment_id`` is a UUID4."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36
        finally:
            await collector.shutdown()

    async def test_start_writes_id_file_to_disk(self, tmp_path: Path) -> None:
        """``start()`` persists the UUID atomically via ``os.open``."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            id_file = tmp_path / "telemetry_id"
            assert id_file.exists()
            assert (
                id_file.read_text(encoding="utf-8").strip() == collector.deployment_id
            )
        finally:
            await collector.shutdown()

    async def test_start_uses_to_thread_for_blocking_io(self, tmp_path: Path) -> None:
        """The deployment-id load goes through ``asyncio.to_thread`` (#1600).

        Spies on ``asyncio.to_thread`` and asserts at least one call
        matched the load helper. We do not pin the exact call count
        because it is an internal implementation detail (one to_thread
        for the whole load is the ideal; counting more strictly would
        couple the test to the helper's shape).
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        original_to_thread = asyncio.to_thread
        seen_callables: list[Callable[..., Any]] = []

        async def spy_to_thread(
            func: Callable[..., Any], *args: object, **kwargs: object
        ) -> object:
            seen_callables.append(func)
            return await original_to_thread(func, *args, **kwargs)

        try:
            with patch(
                "synthorg.telemetry.collector.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                await collector.start()
            assert seen_callables, (
                "start() must offload the deployment-id load through "
                "asyncio.to_thread (zero blocking syscalls on the loop)"
            )
        finally:
            await collector.shutdown()

    async def test_start_does_not_block_event_loop(self, tmp_path: Path) -> None:
        """A scheduled callback fires *during* the load, not after it.

        Records three event-ordering markers in a list:
        ``slow_func_start`` (the sync helper enters the executor
        thread), ``heartbeat_fired`` (scheduled on the loop from the
        to_thread spy), and ``slow_func_end`` (the sync helper
        finishes). If ``start()`` correctly offloads via
        ``asyncio.to_thread``, the event loop runs the heartbeat
        callback BETWEEN the start and end markers. If the load is
        on the loop's thread, the heartbeat fires only after
        ``slow_func_end``.

        Synchronisation is event-driven (``threading.Event`` + an
        explicit ``call_soon_threadsafe``) rather than wall-clock
        sleep, so the assertion is deterministic under CI scheduler
        jitter rather than timing-sensitive.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        loop = asyncio.get_running_loop()
        events: list[str] = []
        heartbeat_seen = threading.Event()

        def record_heartbeat() -> None:
            events.append("heartbeat_fired")
            heartbeat_seen.set()

        original_to_thread = asyncio.to_thread

        def instrumented_helper(
            inner: Callable[..., Any], *args: object, **kwargs: object
        ) -> Any:
            events.append("slow_func_start")
            # Ask the loop (running on a different thread) to fire
            # the heartbeat callback NOW. Waiting on the threading
            # Event proves the loop is not blocked: if the load were
            # running on the loop thread itself, the callback would
            # be queued behind us and the wait would time out.
            loop.call_soon_threadsafe(record_heartbeat)
            if not heartbeat_seen.wait(timeout=2.0):
                msg = "loop did not run the heartbeat callback within 2 s"
                raise AssertionError(msg)
            try:
                return inner(*args, **kwargs)
            finally:
                events.append("slow_func_end")

        async def slow_to_thread(
            func: Callable[..., Any], *args: Any, **kwargs: Any
        ) -> object:
            return await original_to_thread(instrumented_helper, func, *args, **kwargs)

        try:
            with patch(
                "synthorg.telemetry.collector.asyncio.to_thread",
                side_effect=slow_to_thread,
            ):
                await collector.start()
            assert "heartbeat_fired" in events, (
                f"heartbeat never fired during load; events were: {events}"
            )
            slow_start = events.index("slow_func_start")
            heartbeat = events.index("heartbeat_fired")
            slow_end = events.index("slow_func_end")
            assert slow_start < heartbeat < slow_end, (
                "heartbeat fired outside the load window; the load is "
                f"blocking the event loop. events: {events}"
            )
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestStartIsConcurrencySafe:
    """Concurrent ``start()`` calls converge to a single ID file."""

    async def test_concurrent_start_creates_one_id_file(self, tmp_path: Path) -> None:
        """Two parallel ``start()`` calls do not double-write the file.

        The lifecycle lock must serialise the load; the second
        coroutine sees ``deployment_id is not None`` and skips the
        load entirely.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        original_os_open = os.open
        excl_calls: list[int] = []

        def counting_os_open(
            path: str,
            flags: int,
            mode: int = 0o600,
        ) -> int:
            if flags & os.O_EXCL:
                excl_calls.append(flags)
            return original_os_open(path, flags, mode)

        try:
            with patch(
                "synthorg.telemetry.collector.os.open",
                side_effect=counting_os_open,
            ):
                await asyncio.gather(collector.start(), collector.start())
            # Two starts can each cross to_thread, but the lock means
            # the second sees a populated ``_deployment_id`` and never
            # reaches the atomic-create sink.
            assert len(excl_calls) <= 1, (
                "concurrent start() raced past the lifecycle lock; "
                f"saw {len(excl_calls)} exclusive opens"
            )
            assert collector.deployment_id is not None
        finally:
            await collector.shutdown()

    async def test_double_start_is_idempotent(self, tmp_path: Path) -> None:
        """Sequential second ``start()`` does not reload or re-write."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            first_id = collector.deployment_id

            original_os_open = os.open
            excl_calls: list[int] = []

            def counting_os_open(
                path: str,
                flags: int,
                mode: int = 0o600,
            ) -> int:
                if flags & os.O_EXCL:
                    excl_calls.append(flags)
                return original_os_open(path, flags, mode)

            with patch(
                "synthorg.telemetry.collector.os.open",
                side_effect=counting_os_open,
            ):
                await collector.start()

            assert collector.deployment_id == first_id
            assert excl_calls == [], (
                "second start() must not re-attempt the atomic create"
            )
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestDisabledCollectorPerformsNoIo:
    """A disabled collector never touches the filesystem.

    Already true in the prior implementation; pinned here so the
    refactor cannot regress the "disabled leaves no on-disk trace"
    privacy contract.
    """

    async def test_disabled_start_does_not_touch_filesystem(
        self, tmp_path: Path
    ) -> None:
        config = TelemetryConfig(enabled=False)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        def patched_os_open(*_args: object, **_kwargs: object) -> int:
            msg = "os.open called by a disabled collector"
            raise AssertionError(msg)

        try:
            with patch(
                "synthorg.telemetry.collector.os.open",
                side_effect=patched_os_open,
            ):
                await collector.start()
            assert collector.deployment_id is None
            id_file = tmp_path / "telemetry_id"
            assert not id_file.exists()
        finally:
            await collector.shutdown()

    async def test_disabled_collector_does_not_create_data_dir(
        self, tmp_path: Path
    ) -> None:
        """A disabled collector never materialises its data_dir.

        Catches a regression where the disabled-guard moves below the
        ``os.makedirs`` call in the sync helper. The data_dir is
        passed as a not-yet-existing subdirectory; the disabled
        collector must leave it absent.
        """
        config = TelemetryConfig(enabled=False)
        data_dir = tmp_path / "never_created"
        collector = TelemetryCollector(config=config, data_dir=data_dir)
        try:
            await collector.start()
            assert not data_dir.exists()
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestStartLoadsExistingFileEmitsEvents:
    """Event emission on the load + create paths (#1600).

    Pins the new ``TELEMETRY_DEPLOYMENT_ID_LOADED`` and
    ``TELEMETRY_DEPLOYMENT_ID_CREATED`` constants. Without these
    tests, a future refactor could silently drop the
    ``logger.debug(...)`` call and lose the observability signal.
    """

    async def test_existing_file_emits_loaded_event(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        existing_id = "12345678-1234-5678-1234-567812345678"
        (tmp_path / "telemetry_id").write_text(existing_id, encoding="utf-8")
        try:
            with structlog.testing.capture_logs() as logs:
                await collector.start()
            events = [log["event"] for log in logs]
            assert TELEMETRY_DEPLOYMENT_ID_LOADED in events, (
                f"expected TELEMETRY_DEPLOYMENT_ID_LOADED, got events: {events}"
            )
            assert collector.deployment_id == existing_id
        finally:
            await collector.shutdown()

    async def test_new_file_emits_created_event(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            with structlog.testing.capture_logs() as logs:
                await collector.start()
            events = [log["event"] for log in logs]
            assert TELEMETRY_DEPLOYMENT_ID_CREATED in events, (
                f"expected TELEMETRY_DEPLOYMENT_ID_CREATED, got events: {events}"
            )
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestStartIdempotenceAndSideEffects:
    """``start()`` lifecycle invariants beyond the basic happy path."""

    async def test_second_start_skips_to_thread_when_id_already_loaded(
        self, tmp_path: Path
    ) -> None:
        """Sequential second ``start()`` does not re-enter ``to_thread``.

        Validates the ``if self._deployment_id is None`` guard. If
        the guard is regressed, the spy will see two calls.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()

            original_to_thread = asyncio.to_thread
            call_count = 0

            async def counting_to_thread(
                func: Callable[..., Any], *args: Any, **kwargs: Any
            ) -> object:
                nonlocal call_count
                call_count += 1
                return await original_to_thread(func, *args, **kwargs)

            with patch(
                "synthorg.telemetry.collector.asyncio.to_thread",
                side_effect=counting_to_thread,
            ):
                await collector.start()

            assert call_count == 0, (
                f"second start() re-entered to_thread {call_count}x; "
                "the deployment_id guard is regressed"
            )
        finally:
            await collector.shutdown()

    async def test_start_creates_heartbeat_task(self, tmp_path: Path) -> None:
        """``start()`` schedules the heartbeat after the load completes.

        A regression that drops the ``asyncio.create_task(...)`` call
        would leave ``_heartbeat_task`` as ``None``; production would
        stop emitting heartbeats silently.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            heartbeat_task = collector._heartbeat_task
            assert heartbeat_task is not None
            assert not heartbeat_task.done()
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestShutdownGuards:
    """``shutdown()`` invariants under abnormal lifecycles."""

    async def test_shutdown_without_start_is_safe(self, tmp_path: Path) -> None:
        """``shutdown()`` on an enabled collector that never had ``start()``
        called returns cleanly and emits the WARNING signal.

        Without the guard at ``shutdown()``, ``_build_event``'s
        non-None assertion would crash the whole shutdown path.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        # No await collector.start() here.
        with structlog.testing.capture_logs() as logs:
            await collector.shutdown()
        assert collector.deployment_id is None
        events = [log["event"] for log in logs]
        assert TELEMETRY_SHUTDOWN_WITHOUT_START in events


@pytest.mark.unit
class TestCorruptDeploymentIdFile:
    """Existing-but-corrupt ID file falls back to a new UUID (#1600)."""

    @pytest.mark.parametrize(
        "stored_content",
        [
            "not-a-uuid",
            "12345678-1234",  # truncated
            "",  # empty after strip
            "        ",  # whitespace only
        ],
    )
    async def test_corrupt_file_generates_new_id(
        self, tmp_path: Path, stored_content: str
    ) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        (tmp_path / "telemetry_id").write_text(stored_content, encoding="utf-8")
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36
            assert collector.deployment_id != stored_content.strip()
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestThreeWayInstanceRace:
    """Three independent collectors converge to one deployment ID."""

    async def test_three_concurrent_instances_share_one_id(
        self, tmp_path: Path
    ) -> None:
        """Three replicas racing on the same data_dir converge.

        Exercises the ``O_CREAT|O_EXCL`` + peer-recovery path more
        aggressively than the two-coroutine same-instance test does.
        At most one replica wins the atomic create; the other two
        re-read its UUID.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        c1 = TelemetryCollector(config=config, data_dir=tmp_path)
        c2 = TelemetryCollector(config=config, data_dir=tmp_path)
        c3 = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await asyncio.gather(c1.start(), c2.start(), c3.start())
            assert c1.deployment_id is not None
            assert c1.deployment_id == c2.deployment_id == c3.deployment_id
            id_file = tmp_path / "telemetry_id"
            assert id_file.exists()
            assert id_file.read_text(encoding="utf-8").strip() == c1.deployment_id
        finally:
            await c1.shutdown()
            await c2.shutdown()
            await c3.shutdown()


@pytest.mark.unit
class TestStartDeadlineBound:
    """``start()`` enforces a hard deadline so a hung filesystem cannot block boot."""

    async def test_start_falls_back_to_generated_id_on_deadline(
        self, tmp_path: Path
    ) -> None:
        """A loader that exceeds the ``asyncio.wait_for`` budget yields a fallback UUID.

        We patch ``asyncio.wait_for`` to raise ``TimeoutError`` directly
        rather than really blocking for five seconds; the test pins the
        contract that the fallback path engages without hanging the
        suite.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        async def fake_wait_for(*_args: object, **_kwargs: object) -> str:
            raise TimeoutError

        try:
            with (
                patch(
                    "synthorg.telemetry.collector.asyncio.wait_for",
                    side_effect=fake_wait_for,
                ),
                structlog.testing.capture_logs() as logs,
            ):
                await collector.start()
            # A UUID4 fallback must have been assigned.
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36
            # The fallback path must NOT emit the "loaded from disk"
            # event; the deployment is now an in-memory splinter.
            events = [log["event"] for log in logs]
            assert TELEMETRY_DEPLOYMENT_ID_LOADED not in events
            # And the fallback log must flag itself so dashboards can
            # detect splinter deployments.
            assert any(log.get("using_generated_id") is True for log in logs), (
                f"expected using_generated_id=True in {logs!r}"
            )
        finally:
            await collector.shutdown()
