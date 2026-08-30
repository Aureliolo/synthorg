"""Tests for DockerSandboxBackgroundMixin with mocked aiodocker."""

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.errors import (
    SandboxBackgroundJobLimitError,
    SandboxBackgroundJobNotFoundError,
    SandboxBackgroundNoReusableContainerError,
    SandboxBackgroundUnsupportedError,
    SandboxStartError,
)
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit
_DOCKER_MODULE = "synthorg.tools.sandbox.docker_sandbox.aiodocker"

_TEST_OWNER = NotBlankStr("agent-1")
#: What ``_TEST_OWNER`` resolves to once passed through
#: ``_resolve_background_owner_key`` under ``category="terminal"`` (a
#: writable category, so the mount-mode segment is always ``rw``) with
#: no project/image context bound -- the key every persisted row and
#: every read-path lookup must agree on.
_TEST_OWNER_KEY = NotBlankStr("agent-1:rw")
_TERMINAL_CATEGORY = "terminal"


class _InMemoryBackgroundJobRepository:
    """Minimal in-memory double satisfying ``BackgroundJobRepository``."""

    def __init__(self) -> None:
        self._rows: dict[str, BackgroundJobRecord] = {}

    async def save(self, entity: BackgroundJobRecord, /) -> None:
        self._rows[entity.job_id] = entity

    async def save_if_live(self, entity: BackgroundJobRecord, /) -> bool:
        current = self._rows.get(entity.job_id)
        if current is None or current.status not in {
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RUNNING,
        }:
            return False
        self._rows[entity.job_id] = entity
        return True

    async def get(self, entity_id: str, /) -> BackgroundJobRecord | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str, /) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        ordered = sorted(self._rows.values(), key=lambda r: r.job_id)
        return tuple(ordered[offset : offset + limit])

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
        return tuple(self._rows.values())

    async def list_by_container(
        self,
        container_id: str,
        *,
        statuses: frozenset[BackgroundJobStatus] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [
            r
            for r in self._rows.values()
            if r.container_id == container_id
            and (statuses is None or r.status in statuses)
        ]
        return tuple(matches[offset : offset + limit])

    async def count_live_by_owner(self, owner_id: str) -> int:
        return sum(
            1
            for r in self._rows.values()
            if r.owner_id == owner_id
            and r.status in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING}
        )

    async def list_by_owner(
        self, owner_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [r for r in self._rows.values() if r.owner_id == owner_id]
        return tuple(matches[offset : offset + limit])


class _FakeExecMessage:
    """Mimics ``aiodocker.stream.Message`` (``stream``/``data``)."""

    def __init__(self, stream: int, data: bytes) -> None:
        self.stream = stream
        self.data = data


def _make_exec_stream(*, stdout: bytes) -> MagicMock:
    """Build a fake aiodocker exec ``Stream`` yielding one stdout frame then EOF."""
    stream = MagicMock()
    frames: list[_FakeExecMessage | None] = []
    if stdout:
        frames.append(_FakeExecMessage(1, stdout))
    frames.append(None)
    stream.read_out = AsyncMock(side_effect=frames)
    stream.close = AsyncMock()
    return stream


def _install_scripted_exec(
    container_obj: MagicMock, responder: Callable[[str], bytes]
) -> None:
    """Wire ``container.exec()`` to answer based on the script it was given.

    *responder* receives the joined ``cmd`` argv (the wrapper's own
    built script text lives in the last element) and returns the
    stdout bytes to yield.
    """

    def _new_exec(*_args: object, **kwargs: object) -> MagicMock:
        cmd = kwargs.get("cmd") or ()
        cmd_seq = cmd if isinstance(cmd, list | tuple) else ()
        script = str(cmd_seq[-1]) if cmd_seq else ""
        exec_obj = MagicMock()
        exec_obj.start = MagicMock(
            return_value=_make_exec_stream(stdout=responder(script))
        )
        exec_obj.inspect = AsyncMock(return_value={"ExitCode": 0})
        return exec_obj

    container_obj.exec = AsyncMock(side_effect=_new_exec)


def _make_mock_docker(responder: Callable[[str], bytes]) -> MagicMock:
    """Create a mock aiodocker.Docker client scripted by *responder*."""
    mock_docker = MagicMock()
    mock_docker.version = AsyncMock(return_value={"ApiVersion": "1.43"})
    mock_docker.close = AsyncMock()

    mock_containers = MagicMock()
    mock_docker.containers = mock_containers

    mock_created_container = MagicMock()
    mock_created_container.id = "abc123def456"
    mock_containers.create = AsyncMock(return_value=mock_created_container)

    mock_container_obj = MagicMock()
    mock_container_obj.start = AsyncMock()
    mock_container_obj.show = AsyncMock(return_value={"State": {"Running": True}})
    mock_container_obj.stop = AsyncMock()
    mock_container_obj.delete = AsyncMock()
    _install_scripted_exec(mock_container_obj, responder)

    mock_containers.container = MagicMock(return_value=mock_container_obj)
    return mock_docker


@contextmanager
def _patch_aiodocker(mock_docker: MagicMock) -> Iterator[MagicMock]:
    mock_module = MagicMock()
    mock_module.Docker = MagicMock(return_value=mock_docker)
    with patch(_DOCKER_MODULE, mock_module) as p:
        yield p


def _responder_for(pid: str = "4242") -> Callable[[str], bytes]:
    """Return a responder answering pid-confirm / RUNNING / anything else empty."""

    def _respond(script: str) -> bytes:
        if "child_pid=$!" in script:
            return f"{pid}\n".encode()
        if 'echo "RUNNING"' in script:
            return b"RUNNING\n"
        return b""

    return _respond


def _make_sandbox(
    tmp_path: Path, *, docker: MagicMock, registry: BackgroundJobRegistry | None
) -> DockerSandbox:
    """Build a sandbox under a reusable (per-agent) lifecycle strategy.

    ``DockerSandbox``'s own default (``lifecycle_strategy=None``) is
    ``PerCallStrategy``, which every background call must refuse:
    most of these tests need a reusable strategy instead, so
    that refusal is tested explicitly in its own case rather than
    accidentally by every other one.
    """
    return DockerSandbox(
        workspace=tmp_path,
        lifecycle_strategy=PerAgentStrategy(SandboxLifecycleConfig()),
        background_jobs=registry,
    )


class TestStartBackground:
    async def test_refuses_when_no_registry_wired(self, tmp_path: Path) -> None:
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=None)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundUnsupportedError),
        ):
            await sandbox.start_background(
                command="sleep",
                args=("30",),
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )

    async def test_refuses_under_per_call_strategy(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(_responder_for())
        sandbox = DockerSandbox(
            workspace=tmp_path,
            lifecycle_strategy=PerCallStrategy(),
            background_jobs=registry,
        )
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundNoReusableContainerError),
        ):
            await sandbox.start_background(
                command="sleep",
                args=("30",),
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )

    async def test_refuses_at_job_limit(self, tmp_path: Path) -> None:
        repo = _InMemoryBackgroundJobRepository()
        clock = FakeClock()
        registry = BackgroundJobRegistry(repo, clock=clock)
        for i in range(5):
            await registry.save(
                BackgroundJobRecord(
                    job_id=f"existing-{i}",
                    container_id="c1",
                    owner_id=_TEST_OWNER_KEY,
                    command_repr="sleep 5",
                    status=BackgroundJobStatus.RUNNING,
                    output_path="/tmp/.synthorg-jobs/x/output",  # noqa: S108
                    started_at=clock.now(),
                    updated_at=clock.now(),
                    max_duration_seconds=3600.0,
                )
            )
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobLimitError),
        ):
            await sandbox.start_background(
                command="sleep",
                args=("30",),
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )

    async def test_raises_when_pid_never_confirmed(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(lambda _script: b"")
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker), pytest.raises(SandboxStartError):
            await sandbox.start_background(
                command="sleep",
                args=("30",),
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )
        # No record persisted for a job that never actually started.
        assert await registry.list_by_owner(_TEST_OWNER_KEY) == ()

    async def test_persists_a_running_record_on_success(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=FakeClock()
        )
        docker = _make_mock_docker(_responder_for(pid="777"))
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            job_id = await sandbox.start_background(
                command="sleep",
                args=("30",),
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )
        record = await registry.get(job_id)
        assert record is not None
        assert record.status == BackgroundJobStatus.RUNNING
        assert record.pid == 777
        assert record.owner_id == _TEST_OWNER_KEY
        assert record.command_repr.startswith("sleep 30")


class TestPollBackground:
    async def test_not_found_raises(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.poll_background(NotBlankStr("ghost"))

    async def test_still_running_leaves_record_unchanged(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="sleep 30",
            pid=42,
            status=BackgroundJobStatus.RUNNING,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            polled = await sandbox.poll_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert polled.status == BackgroundJobStatus.RUNNING

    async def test_completed_updates_status_and_exit_code(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="exit 3",
            pid=42,
            status=BackgroundJobStatus.RUNNING,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)
        docker = _make_mock_docker(lambda _script: b"3\n")
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            polled = await sandbox.poll_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert polled.status == BackgroundJobStatus.FAILED
        assert polled.exit_code == 3

    async def test_already_terminal_does_not_reprobe(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="true",
            pid=42,
            status=BackgroundJobStatus.COMPLETED,
            exit_code=0,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)

        def _fail(_script: str) -> bytes:
            msg = "should never be called for a terminal job"
            raise AssertionError(msg)

        docker = _make_mock_docker(_fail)
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            polled = await sandbox.poll_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert polled.status == BackgroundJobStatus.COMPLETED


class TestReadBackgroundOutput:
    async def test_not_found_raises(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.read_background_output(NotBlankStr("ghost"), byte_cap=100)

    async def test_returns_captured_output(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="echo hi",
            pid=42,
            status=BackgroundJobStatus.COMPLETED,
            exit_code=0,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)
        docker = _make_mock_docker(lambda _script: b"hi\n")
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            output = await sandbox.read_background_output(
                NotBlankStr("job-1"),
                byte_cap=100,
                category=_TERMINAL_CATEGORY,
                owner_id=_TEST_OWNER,
            )
        assert output == "hi\n"


class TestCancelBackground:
    async def test_not_found_raises(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.cancel_background(NotBlankStr("ghost"))

    async def test_kills_and_marks_cancelled(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="sleep 300",
            pid=42,
            status=BackgroundJobStatus.RUNNING,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            cancelled = await sandbox.cancel_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert cancelled.status == BackgroundJobStatus.CANCELLED

    async def test_already_terminal_returns_unchanged(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        record = BackgroundJobRecord(
            job_id="job-1",
            container_id="abc123def456",
            owner_id=_TEST_OWNER_KEY,
            command_repr="true",
            pid=42,
            status=BackgroundJobStatus.COMPLETED,
            exit_code=0,
            output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
            started_at=clock.now(),
            updated_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)

        def _fail(_script: str) -> bytes:
            msg = "should never kill an already-terminal job"
            raise AssertionError(msg)

        docker = _make_mock_docker(_fail)
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            result = await sandbox.cancel_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert result.status == BackgroundJobStatus.COMPLETED


class TestCrossOwnerAccessRefused:
    """A job's owner is the only caller who can poll/read/cancel it.

    Regression coverage for the CRITICAL access-control gap: before
    ``_get_owned_job``, all three methods did a bare ``registry.get(job_id)``
    with no ownership check at all, so knowing another owner's job id (from a
    log line, a shared task, or ``list_background_jobs``) was enough to reach
    it across agent and project boundaries.
    """

    async def _saved_job(
        self, registry: BackgroundJobRegistry, clock: FakeClock
    ) -> None:
        await registry.save(
            BackgroundJobRecord(
                job_id="job-1",
                container_id="abc123def456",
                owner_id=_TEST_OWNER_KEY,
                command_repr="sleep 300",
                pid=42,
                status=BackgroundJobStatus.RUNNING,
                output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
                started_at=clock.now(),
                updated_at=clock.now(),
                max_duration_seconds=3600.0,
            )
        )

    async def test_poll_refuses_a_different_owner(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await self._saved_job(registry, clock)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.poll_background(
                NotBlankStr("job-1"),
                category=_TERMINAL_CATEGORY,
                owner_id=NotBlankStr("agent-2"),
            )

    async def test_read_output_refuses_a_different_owner(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await self._saved_job(registry, clock)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.read_background_output(
                NotBlankStr("job-1"),
                byte_cap=100,
                category=_TERMINAL_CATEGORY,
                owner_id=NotBlankStr("agent-2"),
            )

    async def test_cancel_refuses_a_different_owner(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await self._saved_job(registry, clock)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with (
            _patch_aiodocker(docker),
            pytest.raises(SandboxBackgroundJobNotFoundError),
        ):
            await sandbox.cancel_background(
                NotBlankStr("job-1"),
                category=_TERMINAL_CATEGORY,
                owner_id=NotBlankStr("agent-2"),
            )

    async def test_rightful_owner_still_succeeds(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await self._saved_job(registry, clock)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            polled = await sandbox.poll_background(
                NotBlankStr("job-1"), category=_TERMINAL_CATEGORY, owner_id=_TEST_OWNER
            )
        assert polled.job_id == "job-1"


class TestListBackgroundJobs:
    async def test_returns_empty_without_registry(self, tmp_path: Path) -> None:
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=None)
        assert (
            await sandbox.list_background_jobs(_TEST_OWNER, category=_TERMINAL_CATEGORY)
            == ()
        )

    async def test_scopes_to_owner(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        for owner_key, job_id in [
            (_TEST_OWNER_KEY, "job-a"),
            (NotBlankStr("agent-2:rw"), "job-b"),
        ]:
            await registry.save(
                BackgroundJobRecord(
                    job_id=job_id,
                    container_id="c1",
                    owner_id=owner_key,
                    command_repr="sleep 5",
                    status=BackgroundJobStatus.RUNNING,
                    output_path=f"/tmp/.synthorg-jobs/{job_id}/output",  # noqa: S108
                    started_at=clock.now(),
                    updated_at=clock.now(),
                    max_duration_seconds=3600.0,
                )
            )
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        jobs = await sandbox.list_background_jobs(
            _TEST_OWNER, category=_TERMINAL_CATEGORY
        )
        assert [j.job_id for j in jobs] == ["job-a"]


class TestPinCheck:
    async def test_returns_false_when_no_live_jobs(self, tmp_path: Path) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            assert await sandbox.pin_check("c1") is False

    async def test_returns_true_while_job_is_live(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await registry.save(
            BackgroundJobRecord(
                job_id="job-1",
                container_id="c1",
                owner_id=_TEST_OWNER,
                command_repr="sleep 300",
                pid=42,
                status=BackgroundJobStatus.RUNNING,
                output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
                started_at=clock.now(),
                updated_at=clock.now(),
                max_duration_seconds=3600.0,
            )
        )
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            assert await sandbox.pin_check("c1") is True

    async def test_expires_and_returns_false_past_ceiling(self, tmp_path: Path) -> None:
        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await registry.save(
            BackgroundJobRecord(
                job_id="job-1",
                container_id="c1",
                owner_id=_TEST_OWNER,
                command_repr="sleep 300",
                pid=42,
                status=BackgroundJobStatus.RUNNING,
                output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
                started_at=clock.now(),
                updated_at=clock.now(),
                max_duration_seconds=10.0,
            )
        )
        clock.advance(11)
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            assert await sandbox.pin_check("c1") is False
        record = await registry.get("job-1")
        assert record is not None
        assert record.status == BackgroundJobStatus.TIMED_OUT


class TestDestroyHandleReaping:
    async def test_reaps_live_jobs_on_teardown(self, tmp_path: Path) -> None:
        from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

        clock = FakeClock()
        registry = BackgroundJobRegistry(
            _InMemoryBackgroundJobRepository(), clock=clock
        )
        await registry.save(
            BackgroundJobRecord(
                job_id="job-1",
                container_id="abc123def456",
                owner_id=_TEST_OWNER,
                command_repr="sleep 300",
                pid=42,
                status=BackgroundJobStatus.RUNNING,
                output_path="/tmp/.synthorg-jobs/job-1/output",  # noqa: S108
                started_at=clock.now(),
                updated_at=clock.now(),
                max_duration_seconds=3600.0,
            )
        )
        docker = _make_mock_docker(_responder_for())
        sandbox = _make_sandbox(tmp_path, docker=docker, registry=registry)
        with _patch_aiodocker(docker):
            await sandbox._ensure_docker()
            await sandbox._destroy_handle(ContainerHandle(container_id="abc123def456"))
        record = await registry.get("job-1")
        assert record is not None
        assert record.status == BackgroundJobStatus.ORPHANED
