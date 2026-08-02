"""Unit tests for the streaming one-shot container mixin.

Covers the line-oriented stdout parsing, the stdout-only idle deadline, and
the spawn self-clean paths with no Docker daemon: ``_iter_lines`` is driven by
a scripted frame stream and ``_spawn_stream_container`` by fake collaborators.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Final, override

import aiodocker
import pytest
from aiodocker.stream import Message, Stream

from synthorg.tools.sandbox.docker_sandbox_stream import (
    _MAX_LINE_CHARS,
    DockerSandboxStreamMixin,
)
from synthorg.tools.sandbox.errors import SandboxStartError
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from tests._shared import FakeDockerClient

pytestmark = pytest.mark.unit

_STDOUT: Final[int] = 1
_STDERR: Final[int] = 2


class _FrameStream(Stream):
    """A scripted ``aiodocker`` attach stream for ``_iter_lines``.

    Subclasses the real :class:`Stream` (bypassing its network setup) so the
    typeguard-instrumented ``_iter_lines`` accepts it at the typed boundary.
    Each item is ``(stream_id, data)`` for a frame, ``None`` for EOF, or the
    sentinel ``"hang"`` to block forever (drives the idle-timeout path), or
    ``("slow", stream_id, data, delay)`` to return a frame after *delay*.
    """

    def __init__(self, frames: list[object]) -> None:
        self._frames = list(frames)

    @override
    async def read_out(self) -> Message | None:
        if not self._frames:
            return None
        item = self._frames.pop(0)
        if item == "hang":
            await asyncio.Event().wait()
        if item is None:
            return None
        if isinstance(item, tuple) and item and item[0] == "slow":
            _, stream_id, data, delay = item
            await asyncio.sleep(delay)
            return Message(stream=stream_id, data=data)
        stream_id, data = item  # type: ignore[misc]
        return Message(stream=stream_id, data=data)


async def _collect(frames: list[object], idle: float = 30.0) -> list[str]:
    mixin = DockerSandboxStreamMixin()
    stream = _FrameStream(frames)
    return [line async for line in mixin._iter_lines(stream, idle)]


async def _drain_into(stream: _FrameStream, idle: float, out: list[str]) -> None:
    """Drive ``_iter_lines`` into *out*, preserving partial results if it raises.

    A single awaitable so a ``pytest.raises`` block wraps one statement; the
    append-in-loop (not a comprehension) is deliberate so lines yielded before
    the idle deadline trips remain observable to the caller.
    """
    mixin = DockerSandboxStreamMixin()
    async for line in mixin._iter_lines(stream, idle):
        out.append(line)  # noqa: PERF401 -- keep partial results across the raise


async def test_iter_lines_buffers_across_frames() -> None:
    lines = await _collect([(_STDOUT, b"line1\nli"), (_STDOUT, b"ne2\n"), None])
    assert lines == ["line1", "line2"]


async def test_iter_lines_flushes_non_terminated_tail_on_eof() -> None:
    lines = await _collect([(_STDOUT, b"no trailing newline"), None])
    assert lines == ["no trailing newline"]


async def test_iter_lines_skips_stderr_frames() -> None:
    lines = await _collect(
        [(_STDERR, b"diagnostic chatter\n"), (_STDOUT, b"real\n"), None]
    )
    assert lines == ["real"]


async def test_iter_lines_truncates_overlong_line() -> None:
    payload = b"x" * (_MAX_LINE_CHARS + 50) + b"\n"
    lines = await _collect([(_STDOUT, payload), None])
    assert len(lines) == 1
    assert len(lines[0]) == _MAX_LINE_CHARS


async def test_iter_lines_times_out_on_idle_stream() -> None:
    collected: list[str] = []
    stream = _FrameStream([(_STDOUT, b"first\n"), "hang"])
    with pytest.raises(SandboxStartError):
        await _drain_into(stream, 0.1, collected)
    # The stdout line before the hang is delivered; then the stdout-only
    # deadline trips on the idle stream.
    assert collected == ["first"]


async def test_iter_lines_stderr_does_not_extend_deadline() -> None:
    # stderr frames spaced past the idle window: the first lands inside the
    # deadline (without resetting it, by design), so the second read_out has
    # less than one full window left and times the stream out. The stderr
    # chatter cannot keep the stream alive, and no stdout line is ever yielded.
    idle = 1.0
    delay = 0.6
    stream = _FrameStream(
        [
            ("slow", _STDERR, b"chatter-1\n", delay),
            ("slow", _STDERR, b"chatter-2\n", delay),
            ("slow", _STDERR, b"chatter-3\n", delay),
        ]
    )
    collected: list[str] = []
    with pytest.raises(SandboxStartError):
        await _drain_into(stream, idle, collected)
    assert collected == []


class _SpawnHarness(DockerSandboxStreamMixin):
    """Fake mixin instance exercising ``_spawn_stream_container`` off-Docker."""

    def __init__(
        self, *, needs_sidecar: bool = False, track_raises: bool = False
    ) -> None:
        self._config = SimpleNamespace(network="bridge")  # type: ignore[assignment]
        self._needs = needs_sidecar
        self._track_raises = track_raises
        self.destroyed: list[ContainerHandle] = []
        self.untracked: list[str] = []

    @override
    def _needs_sidecar(self) -> bool:
        return self._needs

    @override
    async def _bring_up_sidecar(self, docker: aiodocker.Docker) -> str:
        del docker
        return "sidecar-1"

    @override
    def _build_container_config(self, **_kwargs: object) -> dict[str, object]:
        return {}

    @override
    async def _track_container(self, container_id: str, sidecar_id: str | None) -> None:
        del container_id, sidecar_id
        if self._track_raises:
            msg = "tracking backend down"
            raise RuntimeError(msg)

    @override
    async def _untrack_container(self, container_id: str) -> None:
        self.untracked.append(container_id)

    @override
    async def _destroy_handle(self, handle: ContainerHandle) -> None:
        self.destroyed.append(handle)


class _FakeDocker(FakeDockerClient):
    """A Docker client whose only live surface is ``containers.create``."""

    def __init__(
        self,
        *,
        container_id: str = "c-123",
        create_raises: Exception | None = None,
    ) -> None:
        async def _create(_config: object) -> object:
            if create_raises is not None:
                raise create_raises
            return SimpleNamespace(id=container_id)

        create: Callable[[object], Awaitable[object]] = _create
        super().__init__(SimpleNamespace(create=create))


async def test_spawn_self_cleans_when_tracking_fails() -> None:
    harness = _SpawnHarness(track_raises=True)
    with pytest.raises(RuntimeError):
        await harness._spawn_stream_container(
            _FakeDocker(),
            command="python",
            args=(),
            effective_root=Path("/workspace"),
            category="",
        )
    # The just-created container is destroyed rather than stranded.
    assert len(harness.destroyed) == 1
    assert harness.destroyed[0].container_id == "c-123"


async def test_spawn_destroys_sidecar_when_create_fails() -> None:
    harness = _SpawnHarness(needs_sidecar=True)
    with pytest.raises(SandboxStartError):
        await harness._spawn_stream_container(
            _FakeDocker(create_raises=RuntimeError("boom")),
            command="python",
            args=(),
            effective_root=Path("/workspace"),
            category="",
        )
    # A sidecar brought up before the failed create must be torn down.
    assert len(harness.destroyed) == 1
    assert harness.destroyed[0].container_id == "sidecar-1"
    # Its tracking alias must also be dropped, or it lingers stale in the map.
    assert harness.untracked == ["_sidecar:sidecar-1"]


async def test_spawn_untracks_sidecar_alias_on_success() -> None:
    harness = _SpawnHarness(needs_sidecar=True)
    handle = await harness._spawn_stream_container(
        _FakeDocker(container_id="c-777"),
        command="python",
        args=(),
        effective_root=Path("/workspace"),
        category="",
    )
    # The sidecar is folded into the container handle; its standalone tracking
    # alias is dropped so the tracked map does not retain a dead entry per run.
    assert handle.container_id == "c-777"
    assert harness.untracked == ["_sidecar:sidecar-1"]
    assert harness.destroyed == []
