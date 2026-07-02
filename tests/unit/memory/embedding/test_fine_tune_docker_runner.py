"""Ephemeral fine-tune stage containers: fake-docker behaviour tests."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import override

import aiodocker
import pytest

from synthorg.memory.embedding import fine_tune_docker_runner as runner_module
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_docker_runner import (
    PROBE_ENV,
    STAGE_CONFIG_ENV,
    FineTuneContainerRunner,
)
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.embedding.fine_tune_probe_result import parse_probe_line
from synthorg.memory.embedding.fine_tune_stage_executor import (
    DockerStageExecutor,
    StageExecutor,
)
from synthorg.memory.errors import (
    FineTuneCancelledError,
    FineTuneStageExecutionError,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_EXECUTION = FineTuneExecutionConfig(
    backend="docker",
    image="example.test/fine-tune:1",
    memory_limit="2g",
    timeout_seconds=300.0,
)

# Sandbox-grade baseline every fine-tune container must carry; spelled
# out literally so a hardening regression fails the whole-dict asserts.
_HARDENING: dict[str, object] = {
    "CapDrop": ["ALL"],
    "SecurityOpt": ["no-new-privileges"],
    "ReadonlyRootfs": True,
    "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=1g"},  # noqa: S108 -- container path
    "PidsLimit": 256,
}
_STAGE_CACHE_ENV = [
    "HF_HOME=/data/fine-tune/cache/hf",
    "TORCH_HOME=/data/fine-tune/cache/torch",
    "XDG_CACHE_HOME=/data/fine-tune/cache/xdg",
]
_PROBE_CACHE_ENV = [
    "HF_HOME=/tmp/hf",
    "TORCH_HOME=/tmp/torch",
    "XDG_CACHE_HOME=/tmp/xdg",
]


class FakeContainer(aiodocker.containers.DockerContainer):
    """In-memory container double (skips the real ``__init__``)."""

    def __init__(
        self,
        *,
        log_lines: list[str] | None = None,
        exit_code: int = 0,
        hang: bool = False,
    ) -> None:
        self._id = "f" * 64
        self._log_lines = log_lines or []
        self._exit_code = exit_code
        self._hang = hang
        self._released = asyncio.Event()
        self.started = False
        self.stopped = False
        self.deleted = False

    @override
    async def start(self, **kwargs: object) -> None:
        self.started = True

    @override
    def log(  # type: ignore[override]
        self,
        *,
        stdout: bool = False,
        stderr: bool = False,
        follow: bool = False,
        timeout: object = None,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        async def _stream() -> AsyncIterator[str]:
            for line in self._log_lines:
                yield line
            if self._hang:
                await self._released.wait()

        return _stream()

    @override
    async def wait(
        self, *, timeout: object = None, **kwargs: object
    ) -> dict[str, object]:
        if self._hang:
            await self._released.wait()
        return {"StatusCode": self._exit_code}

    @override
    async def stop(
        self,
        *,
        t: int | None = None,
        signal: str | None = None,
        timeout: object = None,
    ) -> None:
        self.stopped = True
        self._released.set()

    @override
    async def delete(
        self,
        *,
        force: bool = False,
        v: bool = False,
        link: bool = False,
        timeout: object = None,
    ) -> None:
        self.deleted = True


class FakeContainers(aiodocker.containers.DockerContainers):
    """Records create() configs and hands back the prepared fake."""

    def __init__(
        self,
        fake_container: FakeContainer,
        *,
        create_error: Exception | None = None,
    ) -> None:
        self.fake_container = fake_container
        self.create_error = create_error
        self.created_configs: list[dict[str, object]] = []

    @override
    async def create(
        self,
        config: object,
        *,
        name: str | None = None,
    ) -> FakeContainer:
        self.created_configs.append(dict(config))  # type: ignore[call-overload]
        if self.create_error is not None:
            raise self.create_error
        return self.fake_container


class FakeDocker(aiodocker.Docker):
    """Client double: no socket, no session (skips the real ``__init__``)."""

    def __init__(self, containers: FakeContainers) -> None:
        self.containers = containers
        self.closed = False

    @override
    async def version(self) -> dict[str, object]:
        return {"Version": "0.0-test"}

    @override
    async def close(self) -> None:
        self.closed = True


def _runner_with(
    monkeypatch: pytest.MonkeyPatch,
    docker: FakeDocker,
    *,
    clock: FakeClock | None = None,
) -> FineTuneContainerRunner:
    runner = FineTuneContainerRunner(clock=clock)

    async def _connect() -> aiodocker.Docker:
        return docker

    monkeypatch.setattr(FineTuneContainerRunner, "_connect", staticmethod(_connect))
    # Fast poll so cancellation/timeout tests do not wait on real time.
    monkeypatch.setattr(runner_module, "_EXIT_POLL_SECONDS", 0.01)
    return runner


async def _run(
    runner: FineTuneContainerRunner,
    *,
    execution: FineTuneExecutionConfig = _EXECUTION,
    cancellation: CancellationToken | None = None,
    progress: list[float] | None = None,
) -> None:
    def _cb(value: float) -> None:
        if progress is not None:
            progress.append(value)

    await runner.run_stage(
        stage=FineTuneStage.TRAINING,
        config={"stage": "training", "output_dir": "/data/fine-tune"},
        execution=execution,
        data_volume="synthorg-data",
        run_id="run-1",
        progress_callback=_cb,
        cancellation=cancellation,
    )


class TestRunStage:
    async def test_success_parses_progress_and_removes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer(
            log_lines=["STAGE_START:training\n", "PROGRESS:0.5\n", "junk\n"],
        )
        containers = FakeContainers(container)
        docker = FakeDocker(containers)
        progress: list[float] = []

        await _run(_runner_with(monkeypatch, docker), progress=progress)

        assert container.started is True
        assert container.deleted is True
        assert docker.closed is True
        assert progress == [0.5]
        config = containers.created_configs[0]
        assert config["Image"] == "example.test/fine-tune:1"
        assert config["Env"] == [
            f"{STAGE_CONFIG_ENV}="
            + json.dumps({"stage": "training", "output_dir": "/data/fine-tune"}),
            *_STAGE_CACHE_ENV,
        ]
        assert config["Labels"] == {
            "synthorg.managed": "true",
            "synthorg.component": "fine-tune",
            "synthorg.fine_tune.run_id": "run-1",
            "synthorg.fine_tune.stage": "training",
        }
        assert config["HostConfig"] == {
            **_HARDENING,
            "Binds": ["synthorg-data:/data:rw"],
            "Memory": 2 * 1024**3,
        }

    async def test_gpu_enabled_requests_devices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer()
        containers = FakeContainers(container)
        execution = _EXECUTION.model_copy(update={"gpu_enabled": True})

        await _run(
            _runner_with(monkeypatch, FakeDocker(containers)), execution=execution
        )

        assert containers.created_configs[0]["HostConfig"] == {
            **_HARDENING,
            "Binds": ["synthorg-data:/data:rw"],
            "Memory": 2 * 1024**3,
            "DeviceRequests": [
                {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}
            ],
        }

    async def test_nonzero_exit_raises_with_error_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer(log_lines=["ERROR: training exploded\n"], exit_code=1)
        docker = FakeDocker(FakeContainers(container))

        with pytest.raises(FineTuneStageExecutionError, match="training exploded"):
            await _run(_runner_with(monkeypatch, docker))
        assert container.deleted is True
        assert docker.closed is True

    async def test_cancellation_stops_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer(hang=True)
        docker = FakeDocker(FakeContainers(container))
        token = CancellationToken()
        token.cancel()

        with pytest.raises(FineTuneCancelledError):
            await _run(_runner_with(monkeypatch, docker), cancellation=token)
        assert container.stopped is True
        assert container.deleted is True

    async def test_timeout_stops_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer(hang=True)
        docker = FakeDocker(FakeContainers(container))
        execution = _EXECUTION.model_copy(update={"timeout_seconds": 60.0})
        clock = FakeClock()
        runner = _runner_with(monkeypatch, docker, clock=clock)

        # The deadline reads through the clock seam: once the watch loop
        # is polling, jump virtual time past the deadline.
        async def _advance_soon() -> None:
            await asyncio.sleep(0.05)
            clock.advance(120.0)

        advance = asyncio.create_task(_advance_soon())
        with pytest.raises(FineTuneStageExecutionError, match="timeout"):
            await _run(runner, execution=execution)
        await advance
        assert container.stopped is True
        assert container.deleted is True

    async def test_launch_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        containers = FakeContainers(
            FakeContainer(), create_error=RuntimeError("no such image")
        )
        docker = FakeDocker(containers)

        with pytest.raises(FineTuneStageExecutionError, match="could not launch"):
            await _run(_runner_with(monkeypatch, docker))
        assert docker.closed is True

    async def test_daemon_unavailable_propagates_typed_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed daemon connect surfaces as the typed stage error."""
        runner = FineTuneContainerRunner()

        async def _connect() -> aiodocker.Docker:
            msg = "could not reach the Docker daemon"
            raise FineTuneStageExecutionError(msg)

        monkeypatch.setattr(FineTuneContainerRunner, "_connect", staticmethod(_connect))
        with pytest.raises(FineTuneStageExecutionError, match="Docker daemon"):
            await _run(runner)


class TestCleanupContracts:
    """``_stop`` / ``_remove`` are best-effort and never raise."""

    async def test_stop_swallows_daemon_errors(self) -> None:
        class ExplodingStopContainer(FakeContainer):
            @override
            async def stop(
                self,
                *,
                t: int | None = None,
                signal: str | None = None,
                timeout: object = None,
            ) -> None:
                msg = "daemon gone"
                raise RuntimeError(msg)

        await FineTuneContainerRunner._stop(ExplodingStopContainer())

    async def test_remove_swallows_daemon_errors(self) -> None:
        class ExplodingDeleteContainer(FakeContainer):
            @override
            async def delete(
                self,
                *,
                force: bool = False,
                v: bool = False,
                link: bool = False,
                timeout: object = None,
            ) -> None:
                msg = "daemon gone"
                raise RuntimeError(msg)

        await FineTuneContainerRunner._remove(
            ExplodingDeleteContainer(), run_id="run-1", stage="training"
        )


class TestProbe:
    async def test_probe_ok_with_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        container = FakeContainer(
            log_lines=["PROBE_OK gpu=Example GPU 90 vram_gb=24.0\n"],
        )
        containers = FakeContainers(container)
        runner = _runner_with(monkeypatch, FakeDocker(containers))

        result = await runner.probe(image="example.test/fine-tune:1", gpu_enabled=True)

        assert result.ok is True
        assert result.gpu == "Example GPU 90"
        assert result.vram_gb == 24.0
        assert container.deleted is True
        config = containers.created_configs[0]
        assert config["Env"] == [f"{PROBE_ENV}=1", *_PROBE_CACHE_ENV]
        assert config["HostConfig"] == {
            **_HARDENING,
            "Memory": 4 * 1024**3,
            "DeviceRequests": [
                {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}
            ],
        }

    async def test_probe_fail_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        container = FakeContainer(log_lines=["PROBE_FAIL torch import failed\n"])
        runner = _runner_with(monkeypatch, FakeDocker(FakeContainers(container)))

        result = await runner.probe(image="example.test/fine-tune:1", gpu_enabled=False)

        assert result.ok is False
        assert "torch import failed" in result.detail

    async def test_probe_no_marker_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        container = FakeContainer(log_lines=["something unrelated\n"])
        runner = _runner_with(monkeypatch, FakeDocker(FakeContainers(container)))

        result = await runner.probe(image="example.test/fine-tune:1", gpu_enabled=False)

        assert result.ok is False
        assert "no PROBE_OK/PROBE_FAIL" in result.detail

    async def test_probe_create_failure_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        containers = FakeContainers(
            FakeContainer(), create_error=RuntimeError("daemon gone")
        )
        runner = _runner_with(monkeypatch, FakeDocker(containers))

        result = await runner.probe(image="example.test/fine-tune:1", gpu_enabled=False)

        assert result.ok is False
        assert "could not launch" in result.detail


class TestParseProbeLine:
    def test_ok_without_gpu(self) -> None:
        result = parse_probe_line("PROBE_OK gpu=none vram_gb=0")
        assert result.ok is True
        assert result.gpu is None
        assert result.vram_gb is None

    def test_ok_gpu_name_with_spaces(self) -> None:
        result = parse_probe_line("PROBE_OK gpu=Example GPU 90 Ti vram_gb=23.5")
        assert result.gpu == "Example GPU 90 Ti"
        assert result.vram_gb == 23.5

    def test_fail_reason(self) -> None:
        result = parse_probe_line("PROBE_FAIL CUDA unavailable")
        assert result.ok is False
        assert result.detail == "CUDA unavailable"


class TestDockerStageExecutor:
    def test_satisfies_protocol(self) -> None:
        executor = DockerStageExecutor(
            execution=_EXECUTION,
            runner=FineTuneContainerRunner(),
            data_volume="synthorg-data",
        )
        assert isinstance(executor, StageExecutor)

    def test_rejects_non_docker_execution(self) -> None:
        with pytest.raises(ValueError, match="backend='docker'"):
            DockerStageExecutor(
                execution=FineTuneExecutionConfig(backend="in-process"),
                runner=FineTuneContainerRunner(),
                data_volume="synthorg-data",
            )

    async def test_delegates_with_volume_and_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = FakeContainer()
        containers = FakeContainers(container)
        runner = _runner_with(monkeypatch, FakeDocker(containers))
        executor = DockerStageExecutor(
            execution=_EXECUTION, runner=runner, data_volume="custom-volume"
        )

        await executor.run_stage(
            stage=FineTuneStage.MINING_NEGATIVES,
            config={"stage": "mining_negatives"},
            run_id="run-9",
            progress_callback=None,
            cancellation=None,
        )

        config = containers.created_configs[0]
        assert config["HostConfig"] == {
            **_HARDENING,
            "Binds": ["custom-volume:/data:rw"],
            "Memory": 2 * 1024**3,
        }
        assert config["Labels"] == {
            "synthorg.managed": "true",
            "synthorg.component": "fine-tune",
            "synthorg.fine_tune.run_id": "run-9",
            "synthorg.fine_tune.stage": "mining_negatives",
        }
