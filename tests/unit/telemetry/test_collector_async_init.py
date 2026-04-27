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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

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

    def test_constructor_does_not_call_os_open(self, tmp_path: Path) -> None:
        """No ``os.open`` in the constructor when telemetry is enabled.

        Patches the ``os.open`` symbol the collector imports so any
        constructor-time call raises immediately; if the assertion in
        ``patched_os_open`` fires, the constructor is still doing
        atomic-create I/O on the event loop's thread.
        """

        def patched_os_open(*_args: object, **_kwargs: object) -> int:
            msg = "os.open called from constructor"
            raise AssertionError(msg)

        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        with patch(
            "synthorg.telemetry.collector.os.open",
            side_effect=patched_os_open,
        ):
            TelemetryCollector(config=config, data_dir=tmp_path)

    def test_constructor_does_not_call_os_path_exists(self, tmp_path: Path) -> None:
        """No ``os.path.exists`` probe in the constructor."""

        def patched_exists(*_args: object, **_kwargs: object) -> bool:
            msg = "os.path.exists called from constructor"
            raise AssertionError(msg)

        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        with patch(
            "synthorg.telemetry.collector.os.path.exists",
            side_effect=patched_exists,
        ):
            TelemetryCollector(config=config, data_dir=tmp_path)

    def test_constructor_does_not_call_builtin_open(self, tmp_path: Path) -> None:
        """No builtin ``open`` from the constructor.

        Catches a regression where the collector reads the existing
        ID file via ``open(...)`` directly during construction.
        """

        def patched_open(*_args: object, **_kwargs: object) -> object:
            msg = "builtin open() called from constructor"
            raise AssertionError(msg)

        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        with patch("builtins.open", side_effect=patched_open):
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
        """The deployment-id load goes through ``asyncio.to_thread``.

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
        """A heartbeat ``call_soon`` fires while the load is in flight.

        Replaces the sync I/O helper with a slow stand-in (50 ms
        sleep). If ``start()`` correctly offloads via
        ``asyncio.to_thread``, the event loop runs the heartbeat
        callback while the thread blocks; if the load is on the loop,
        the callback only fires after the sleep completes and the
        elapsed time spans the full sleep.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        loop = asyncio.get_running_loop()
        heartbeat_at: list[float] = []

        def record_heartbeat() -> None:
            heartbeat_at.append(loop.time())

        original_to_thread = asyncio.to_thread

        async def slow_to_thread(
            func: Callable[..., Any], *args: Any, **kwargs: Any
        ) -> object:
            # Schedule the heartbeat to fire ~10 ms after to_thread
            # starts. If the loop is blocked, this callback waits
            # behind the sync work.
            loop.call_later(0.01, record_heartbeat)
            return await original_to_thread(
                _slow_then(func, 0.05),
                *args,
                **kwargs,
            )

        try:
            start_t = loop.time()
            with patch(
                "synthorg.telemetry.collector.asyncio.to_thread",
                side_effect=slow_to_thread,
            ):
                await collector.start()
            assert heartbeat_at, "heartbeat never fired during load"
            elapsed_when_heartbeat_fired = heartbeat_at[0] - start_t
            assert elapsed_when_heartbeat_fired < 0.05, (
                "heartbeat fired only after the blocking work finished; "
                "the load is still on the event loop's thread "
                f"(fired at {elapsed_when_heartbeat_fired * 1000:.1f} ms)"
            )
        finally:
            await collector.shutdown()


def _slow_then(func: Callable[..., Any], delay_s: float) -> Callable[..., Any]:
    """Wrap a callable so it sleeps before delegating.

    The sleep happens inside the executor thread the
    ``asyncio.to_thread`` helper hands the wrapper to, so the loop
    is free to schedule callbacks while it blocks. Returns a
    plain callable; the caller passes its own args / kwargs.
    """

    def wrapper(*args: object, **kwargs: object) -> Any:
        time.sleep(delay_s)
        return func(*args, **kwargs)

    return wrapper


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
            mode: int = 0o777,
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
                mode: int = 0o777,
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
