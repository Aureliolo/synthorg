"""Tests for the pinned-container foreground-exec timeout fix.

When a container has live background jobs, a foreground ``execute()``
call must not collaterally kill them on its own timeout. These tests
cover the branch in ``DockerSandboxExecMixin._exec_command`` that checks
``BackgroundJobRegistry.has_live_jobs`` and, when pinned, kills only the
timed-out exec's own process group instead of stopping the container.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.errors import SandboxBackgroundUnpinnedExecutionActiveError
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
        # A FakeClock keeps the pinned-kill path's pidfile-read retry
        # loop (`_read_pinned_pid`) from spending real wall-clock time
        # sleeping between attempts when a test scripts an empty read.
        clock=FakeClock(),
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


def _issued_scripts(container_obj: MagicMock) -> list[str]:
    """The joined ``cmd`` argv text of every exec this container recorded.

    Returns:
        One joined string per ``container.exec()`` call, in call order.
    """
    return [
        " ".join(str(part) for part in call.kwargs.get("cmd", ()))
        for call in container_obj.exec.call_args_list
    ]


class TestUnpinnedPath:
    """No live background jobs: the ordinary, unwrapped exec path."""

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

    async def test_refuses_a_background_job_while_unpinned_exec_is_active(
        self, tmp_path: Path
    ) -> None:
        """A concurrent start_background must not pin to a container an
        in-flight unpinned exec's own timeout can still stop outright.
        """
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(lambda _script: ExecResponse(hang=True))
        sandbox = _make_sandbox(tmp_path, registry=registry)

        with _patch_aiodocker(docker):
            exec_task = asyncio.create_task(
                sandbox.execute(
                    command="sleep",
                    args=("100",),
                    owner_id="agent-1",
                    category="terminal",
                    timeout=0.05,
                )
            )
            try:
                # Let the exec's pin decision (and its reservation) run;
                # it awaits the registry check before hanging on the
                # stream read, so a couple of scheduler turns suffice.
                for _ in range(5):
                    await asyncio.sleep(0)

                with pytest.raises(SandboxBackgroundUnpinnedExecutionActiveError):
                    await sandbox.start_background(
                        command="echo",
                        args=("hi",),
                        owner_id="agent-1",
                        category="terminal",
                    )
            finally:
                await exec_task

        # The reservation is released once the unpinned exec finishes,
        # so it must not linger and lock the owner out permanently.
        assert not sandbox._unpinned_execs_in_flight


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

        assert any(
            "kill -TERM -555" in script for script in _issued_scripts(container_obj)
        )

        sibling = await registry.get("sibling-job")
        assert sibling is not None
        assert sibling.status == BackgroundJobStatus.RUNNING

    async def test_pid_arriving_after_a_late_write_still_kills_only_the_group(
        self, tmp_path: Path
    ) -> None:
        """The first couple of pidfile reads race the wrapper's own write.

        A single empty read must not be treated as final: the retry
        loop in ``_read_pinned_pid`` keeps asking until the pidfile
        appears (or its own deadline elapses), so a slow-but-legitimate
        write still ends in a process-group kill, never a container stop.
        """
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        reads = 0

        def _respond(script: str) -> ExecResponse:
            nonlocal reads
            if "echo $$ >" in script:
                return ExecResponse(hang=True)
            if script.startswith("cat ") or " cat " in script:
                reads += 1
                # Empty on the first two reads (racing the wrapper's own
                # write), a real pid from the third read onward.
                return ExecResponse(stdout=b"" if reads < 3 else b"555\n")
            if "kill -TERM -" in script:
                return ExecResponse(stdout=b"")
            return ExecResponse(stdout=b"")

        docker = _make_mock_docker(_respond)
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        assert reads >= 3
        container_obj.stop.assert_not_awaited()
        assert any(
            "kill -TERM -555" in script for script in _issued_scripts(container_obj)
        )

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
            # Same explicit owner_id as test_timeout_kills_only_the_process_group:
            # without it the container degrades to per-call and gets stopped
            # after every exec regardless of this fix, which would make
            # ``stop.assert_awaited_once()`` pass for the wrong reason.
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        assert any(
            script.startswith("bash") and "cat " in script
            for script in _issued_scripts(container_obj)
        ), "pid-read control exec was never issued"
        container_obj.stop.assert_awaited_once()

    async def test_pid_zero_falls_back_to_stop_container(self, tmp_path: Path) -> None:
        """``0`` parses as a digit but is not a positive pid, unlike an
        empty or non-numeric pidfile -- both must be refused the same way.
        """
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        docker = _make_mock_docker(_pinned_responder(main_hang=True, pid_reply=b"0\n"))
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        container_obj.stop.assert_awaited_once()

    async def test_non_ascii_digit_pid_falls_back_to_stop_container(
        self, tmp_path: Path
    ) -> None:
        """A Unicode digit character (e.g. superscript "²") satisfies
        ``str.isdigit()`` but raises ``ValueError`` from ``int()`` --
        the read must still be treated as unreadable, not crash.
        """
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)
        docker = _make_mock_docker(
            _pinned_responder(main_hang=True, pid_reply="²\n".encode())
        )
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        container_obj.stop.assert_awaited_once()

    async def test_pid_read_exception_falls_back_to_stop_container(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)

        def _respond(script: str) -> ExecResponse:
            if "echo $$ >" in script:
                return ExecResponse(hang=True)
            if "cat " in script:
                msg = "pidfile read exec failed to open"
                raise RuntimeError(msg)
            return ExecResponse(stdout=b"")

        docker = _make_mock_docker(_respond)
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        container_obj.stop.assert_awaited_once()

    async def test_kill_call_failure_falls_back_to_stop_container(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)

        def _respond(script: str) -> ExecResponse:
            if "echo $$ >" in script:
                return ExecResponse(hang=True)
            if "cat " in script:
                return ExecResponse(stdout=b"555\n")
            if "kill -TERM -" in script:
                msg = "kill exec failed to open"
                raise RuntimeError(msg)
            return ExecResponse(stdout=b"")

        docker = _make_mock_docker(_respond)
        sandbox = _make_sandbox(tmp_path, registry=registry)
        container_obj = docker.containers.container()

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="sleep",
                args=("100",),
                timeout=0.05,
                owner_id="agent-1",
            )

        assert result.timed_out
        container_obj.stop.assert_awaited_once()

    async def test_cleanup_failure_does_not_affect_the_result(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await _seed_live_job(registry)

        def _respond(script: str) -> ExecResponse:
            if "echo $$ >" in script:
                return ExecResponse(stdout=b"out\n")
            if "rm -rf" in script:
                msg = "cleanup exec failed to open"
                raise RuntimeError(msg)
            return ExecResponse(stdout=b"")

        docker = _make_mock_docker(_respond)
        sandbox = _make_sandbox(tmp_path, registry=registry)

        with _patch_aiodocker(docker):
            result = await sandbox.execute(
                command="echo", args=("out",), owner_id="agent-1"
            )

        assert not result.timed_out
        assert result.stdout == "out\n"

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
