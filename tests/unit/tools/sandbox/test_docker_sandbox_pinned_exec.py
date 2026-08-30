"""Tests for the #2880 pinned-container foreground-exec timeout fix.

When a container has live background jobs, a foreground ``execute()``
call must not collaterally kill them on its own timeout. These tests
cover the branch in ``DockerSandboxExecMixin._exec_command`` that checks
``BackgroundJobRegistry.has_live_jobs`` and, when pinned, kills only the
timed-out exec's own process group instead of stopping the container.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from tests._shared.fake_background_job_exec import (
    ExecResponse,
)
from tests._shared.fake_background_job_exec import (
    make_mock_docker as _make_mock_docker,
)
from tests._shared.fake_background_job_exec import (
    patch_aiodocker as _patch_aiodocker,
)
from tests._shared.fake_background_job_repo import (
    InMemoryBackgroundJobRepository as _InMemoryBackgroundJobRepository,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_CONTAINER_ID = "abc123def456"


def _make_sandbox(
    tmp_path: Path, *, registry: BackgroundJobRegistry | None
) -> DockerSandbox:
    return DockerSandbox(
        workspace=tmp_path,
        lifecycle_strategy=PerAgentStrategy(SandboxLifecycleConfig()),
        background_jobs=registry,
    )


async def _seed_live_job(registry: BackgroundJobRegistry, *, pid: int = 999) -> None:
    clock = FakeClock()
    await registry.save(
        BackgroundJobRecord(
            job_id="sibling-job",
            container_id=_CONTAINER_ID,
            owner_id="agent-1:rw",
            command_repr="sleep 300",
            pid=pid,
            status=BackgroundJobStatus.RUNNING,
            output_path="/tmp/.synthorg-jobs/sibling-job/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
    )


def _pinned_responder(
    *, main_hang: bool, main_stdout: bytes = b"", pid_reply: bytes = b""
) -> Callable[[str], ExecResponse]:
    """Route the exec's script text to the right scripted response.

    Distinguishes the three exec shapes this fix issues: the wrapped
    ``setsid ...; echo $$ > ...; exec <argv>`` main command, the pidfile
    ``cat`` read, and the ``kill -TERM -<pid>`` control exec.
    """

    def _respond(script: str) -> ExecResponse:
        if "echo $$ >" in script:
            return ExecResponse(stdout=main_stdout, hang=main_hang)
        if script.startswith("cat ") or " cat " in script:
            return ExecResponse(stdout=pid_reply)
        if "kill -TERM -" in script:
            return ExecResponse(stdout=b"")
        return ExecResponse(stdout=b"")

    return _respond


class TestUnpinnedPathUnchanged:
    """No live background jobs: today's exact behaviour, unchanged."""

    async def test_no_registry_wired_takes_the_ordinary_path(
        self, tmp_path: Path
    ) -> None:
        docker = _make_mock_docker(lambda _script: ExecResponse(stdout=b"hi\n"))
        sandbox = _make_sandbox(tmp_path, registry=None)

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="echo", args=("hi",))

        assert result.stdout == "hi\n"
        assert not result.timed_out

    async def test_no_live_jobs_takes_the_ordinary_path(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(lambda _script: ExecResponse(stdout=b"hi\n"))
        sandbox = _make_sandbox(tmp_path, registry=registry)

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="echo", args=("hi",))

        assert result.stdout == "hi\n"
        assert not result.timed_out

    async def test_unpinned_timeout_still_stops_the_container(
        self, tmp_path: Path
    ) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(lambda _script: ExecResponse(hang=True))
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="sleep", args=("100",), timeout=0.05)

        assert result.timed_out
        container_obj.stop.assert_awaited_once()


class TestPinnedPath:
    async def test_completes_before_timeout_streams_normally(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        docker = _make_mock_docker(
            _pinned_responder(main_hang=False, main_stdout=b"hello\n")
        )
        sandbox = _make_sandbox(tmp_path, registry=registry)

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="echo", args=("hello",))

        assert result.stdout == "hello\n"
        assert not result.timed_out

    async def test_timeout_kills_only_the_process_group(self, tmp_path: Path) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        docker = _make_mock_docker(
            _pinned_responder(main_hang=True, pid_reply=b"555\n")
        )
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            # An explicit owner_id keeps the container reused (the
            # ordinary shape a live agent/task run establishes via
            # correlation context) rather than degrading to per-call,
            # which would tear the container down regardless of this
            # fix and make "the sibling job survives" untestable.
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        container_obj.stop.assert_not_awaited()
        container_obj.delete.assert_not_awaited()

        sibling = await registry.get("sibling-job")
        assert sibling is not None
        assert sibling.status == BackgroundJobStatus.RUNNING

    async def test_timeout_with_unreadable_pid_falls_back_to_stop_container(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        docker = _make_mock_docker(_pinned_responder(main_hang=True, pid_reply=b""))
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="sleep", args=("100",), timeout=0.05)

        assert result.timed_out
        container_obj.stop.assert_awaited_once()

    async def test_stdout_and_stderr_are_captured_separately(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)

        def _respond(script: str) -> ExecResponse:
            if "echo $$ >" in script:
                return ExecResponse(stdout=b"out\n", stderr=b"err\n")
            return ExecResponse(stdout=b"")

        docker = _make_mock_docker(_respond)
        sandbox = _make_sandbox(tmp_path, registry=registry)

        with _patch_aiodocker(docker):
            result = await sandbox.execute(command="echo", args=("out",))

        assert result.stdout == "out\n"
        assert result.stderr == "err\n"
