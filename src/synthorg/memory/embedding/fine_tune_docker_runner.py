# module-kind: adapter
"""Ephemeral one-shot containers for torch-bound fine-tune stages.

Each stage of a docker-backed run gets its own container: created,
started, log-streamed for the runner's structured markers
(``PROGRESS:<fraction>`` drives the WS progress pipeline,
``ERROR:<message>`` captures the failure detail), and removed
unconditionally on exit. Stage data flows through the shared data
volume mounted read-write at ``/data``; the flat stage config rides in
as an inline JSON env var, so nothing is mounted per stage.

Deliberately not built on ``DockerSandbox``: the sandbox is a
keep-alive container the engine ``exec``s into, hardened with a
read-only rootfs and no ``/data`` access, and has no GPU support.
Training containers are the opposite shape (run-to-completion, ``/data``
read-write, optional GPU passthrough, outbound network for the base
model's hub download), so this launcher shares only the sandbox's
conventions: the ``synthorg.managed`` label (the reconciliation sweep
cleans orphans after a backend crash; interrupted runs are already
marked FAILED and stay resumable) and stop-then-force-remove teardown.
"""

import asyncio
import contextlib
import json
import re
from typing import Final, cast

import aiodocker
import aiodocker.containers
from aiodocker.types import JSONObject
from pydantic import BaseModel, ConfigDict

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage, ProgressCallback
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.errors import FineTuneCancelledError, FineTuneStageExecutionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.fine_tune import (
    FINE_TUNE_CONTAINER_CANCELLED,
    FINE_TUNE_CONTAINER_COMPLETED,
    FINE_TUNE_CONTAINER_FAILED,
    FINE_TUNE_CONTAINER_STARTED,
    FINE_TUNE_CONTAINER_TIMED_OUT,
    FINE_TUNE_PROBE_FAILED,
    FINE_TUNE_PROBE_OK,
    FINE_TUNE_PROBE_STARTED,
)
from synthorg.tools.sandbox._memory_limit import parse_memory_limit

logger = get_logger(__name__)

STAGE_CONFIG_ENV: Final[str] = "SYNTHORG_FINE_TUNE_STAGE_CONFIG"
PROBE_ENV: Final[str] = "SYNTHORG_FINE_TUNE_PROBE"

_MARKER_PROGRESS: Final[str] = "PROGRESS:"
_MARKER_ERROR: Final[str] = "ERROR:"
_PROBE_LINE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^PROBE_(?:OK|FAIL)\b.*$", re.MULTILINE
)

# All GPUs visible to the daemon; per-GPU selection is a deployment
# concern (NVIDIA_VISIBLE_DEVICES on the daemon / compose level).
_GPU_COUNT_ALL: Final[int] = -1
_PIDS_LIMIT: Final[int] = 256
# Poll cadence for the cancellation watcher between exit checks.
_EXIT_POLL_SECONDS: Final[float] = 0.5
# Grace Docker gives the runner's SIGTERM handler before SIGKILL.
_STOP_GRACE_SECONDS: Final[int] = 10
_PROBE_TIMEOUT_SECONDS: Final[float] = 120.0
_PROBE_MEMORY_LIMIT: Final[str] = "4g"
_SHORT_ID_LEN: Final[int] = 12

LABEL_MANAGED: Final[str] = "synthorg.managed"
LABEL_COMPONENT: Final[str] = "synthorg.component"
COMPONENT_FINE_TUNE: Final[str] = "fine-tune"
LABEL_RUN_ID: Final[str] = "synthorg.fine_tune.run_id"
LABEL_STAGE: Final[str] = "synthorg.fine_tune.stage"


class ProbeResult(BaseModel):
    """Outcome of an ephemeral fine-tune image probe.

    Attributes:
        ok: Whether the image booted and its dependencies imported.
        gpu: GPU device name the container saw, or ``None``.
        vram_gb: Total VRAM in GiB, or ``None`` when no GPU.
        detail: Human-readable outcome line for the preflight report.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ok: bool
    gpu: str | None = None
    vram_gb: float | None = None
    detail: str


def parse_probe_line(line: str) -> ProbeResult:
    """Parse the runner's ``PROBE_OK`` / ``PROBE_FAIL`` output line.

    Returns:
        Result of type ``ProbeResult``.
    """
    text = line.strip()
    if text.startswith("PROBE_OK"):
        gpu: str | None = None
        vram: float | None = None
        rest = text.removeprefix("PROBE_OK").strip()
        if " vram_gb=" in rest:
            gpu_part, _, vram_part = rest.rpartition(" vram_gb=")
            gpu_value = gpu_part.removeprefix("gpu=").strip()
            gpu = None if gpu_value in {"", "none"} else gpu_value
            try:
                vram = float(vram_part)
            except ValueError:
                vram = None
            if gpu is None:
                vram = None
        return ProbeResult(ok=True, gpu=gpu, vram_gb=vram, detail=text)
    reason = text.removeprefix("PROBE_FAIL").strip() or "probe failed"
    return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=reason)


class FineTuneContainerRunner:
    """Launches ephemeral one-shot fine-tune stage containers.

    Holds no persistent Docker client: each invocation opens and closes
    its own session (stages run for minutes, so the handshake cost is
    negligible and there is no client to leak at shutdown).

    Args:
        clock: Injectable clock for the stage deadline (tests use
            ``FakeClock``).
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def run_stage(  # noqa: PLR0913 -- stage collaborators threaded explicitly
        self,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        execution: FineTuneExecutionConfig,
        data_volume: str,
        run_id: str,
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Run one stage in an ephemeral container, removed on exit.

        Raises:
            FineTuneCancelledError: When cancellation fired mid-stage.
            FineTuneStageExecutionError: On non-zero exit, timeout, or a
                launch failure (missing image, daemon unavailable).
        """
        docker = await self._connect()
        try:
            await self._run_stage_with_client(
                docker,
                stage=stage,
                config=config,
                execution=execution,
                data_volume=data_volume,
                run_id=run_id,
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        finally:
            await docker.close()

    async def _run_stage_with_client(  # noqa: PLR0913 -- stage collaborators threaded explicitly
        self,
        docker: aiodocker.Docker,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        execution: FineTuneExecutionConfig,
        data_volume: str,
        run_id: str,
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Create, supervise, and always remove one stage container.

        Raises:
            FineTuneCancelledError: When cancellation fired mid-stage.
            FineTuneStageExecutionError: On non-zero exit, timeout, or a
                launch failure.
        """
        container_config = {
            "Image": execution.image,
            "Env": [f"{STAGE_CONFIG_ENV}={json.dumps(config)}"],
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_COMPONENT: COMPONENT_FINE_TUNE,
                LABEL_RUN_ID: run_id,
                LABEL_STAGE: stage.value,
            },
            "HostConfig": _build_host_config(execution, data_volume),
        }
        try:
            container = await docker.containers.create(
                config=cast("JSONObject", container_config),
            )
            await container.start()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                FINE_TUNE_CONTAINER_FAILED,
                run_id=run_id,
                stage=stage.value,
                phase="launch",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"could not launch the {stage.value} stage container"
                f" (image {execution.image}): {safe_error_description(exc)}"
            )
            raise FineTuneStageExecutionError(msg) from exc
        logger.info(
            FINE_TUNE_CONTAINER_STARTED,
            run_id=run_id,
            stage=stage.value,
            container_id=container.id[:_SHORT_ID_LEN],
            image=execution.image,
            gpu_enabled=execution.gpu_enabled,
        )
        try:
            await self._supervise(
                container,
                stage=stage,
                execution=execution,
                run_id=run_id,
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        finally:
            await self._remove(container, run_id=run_id, stage=stage.value)

    async def probe(self, *, image: str, gpu_enabled: bool) -> ProbeResult:
        """Boot a probe container and report dependency/GPU readiness.

        Returns:
            The parsed probe outcome; launch failures come back as a
            failed :class:`ProbeResult` (preflight reports, never raises).
        """
        logger.info(FINE_TUNE_PROBE_STARTED, image=image, gpu_enabled=gpu_enabled)
        try:
            docker = await self._connect()
        except FineTuneStageExecutionError as exc:
            return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=str(exc))
        try:
            return await self._probe_with_client(
                docker, image=image, gpu_enabled=gpu_enabled
            )
        finally:
            await docker.close()

    async def _probe_with_client(
        self,
        docker: aiodocker.Docker,
        *,
        image: str,
        gpu_enabled: bool,
    ) -> ProbeResult:
        """Create, drain, and always remove one probe container.

        Returns:
            Result of type ``ProbeResult``.
        """
        host_config: dict[str, object] = {
            "Memory": parse_memory_limit(_PROBE_MEMORY_LIMIT),
            "PidsLimit": _PIDS_LIMIT,
        }
        if gpu_enabled:
            host_config["DeviceRequests"] = _gpu_device_requests()
        container_config = {
            "Image": image,
            "Env": [f"{PROBE_ENV}=1"],
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_COMPONENT: COMPONENT_FINE_TUNE,
                LABEL_STAGE: "probe",
            },
            "HostConfig": host_config,
        }
        try:
            container = await docker.containers.create(
                config=cast("JSONObject", container_config),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            detail = f"probe container creation failed: {safe_error_description(exc)}"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=detail)
        try:
            await container.start()
            output = await asyncio.wait_for(
                self._drain_probe_output(container),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            detail = f"probe timed out after {_PROBE_TIMEOUT_SECONDS:.0f}s"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=detail)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            detail = f"probe failed to run: {safe_error_description(exc)}"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=detail)
        finally:
            await self._remove(container, run_id="probe", stage="probe")
        match = _PROBE_LINE_PATTERN.search(output)
        if match is None:
            detail = "probe produced no PROBE_OK/PROBE_FAIL line"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, gpu=None, vram_gb=None, detail=detail)
        result = parse_probe_line(match.group(0))
        if result.ok:
            logger.info(
                FINE_TUNE_PROBE_OK,
                image=image,
                gpu=result.gpu,
                vram_gb=result.vram_gb,
            )
        else:
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=result.detail)
        return result

    # -- Internals ------------------------------------------------------

    @staticmethod
    async def _connect() -> aiodocker.Docker:
        """Open a Docker client and verify the daemon answers.

        Returns:
            An ``aiodocker.Docker`` client the caller must close.

        Raises:
            FineTuneStageExecutionError: If the daemon is unavailable.
        """
        client = aiodocker.Docker()
        try:
            await client.version()
        except Exception as exc:
            reraise_critical(exc)
            await client.close()
            msg = (
                "Docker daemon unavailable for fine-tune stage"
                f" containers: {safe_error_description(exc)}"
            )
            raise FineTuneStageExecutionError(msg) from exc
        return client

    async def _supervise(  # noqa: PLR0913 -- stage collaborators threaded explicitly
        self,
        container: aiodocker.containers.DockerContainer,
        *,
        stage: FineTuneStage,
        execution: FineTuneExecutionConfig,
        run_id: str,
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Stream markers until exit, enforcing timeout and cancellation.

        Raises:
            FineTuneCancelledError: When the cancellation token fired.
            FineTuneStageExecutionError: On timeout or non-zero exit.
        """
        error_lines: list[str] = []
        exit_task = asyncio.create_task(
            self._await_exit(container, progress_callback, error_lines),
        )
        try:
            exit_code = await self._watch_until_exit(
                container,
                exit_task,
                stage=stage,
                execution=execution,
                run_id=run_id,
                cancellation=cancellation,
            )
        except BaseException:
            if not exit_task.done():
                exit_task.cancel()
                # Absorb the reaped task's outcome so it cannot mask the
                # in-flight cancellation/timeout error being raised.
                await asyncio.gather(exit_task, return_exceptions=True)
            raise
        if exit_code != 0:
            detail = "; ".join(error_lines) if error_lines else "no ERROR marker"
            logger.warning(
                FINE_TUNE_CONTAINER_FAILED,
                run_id=run_id,
                stage=stage.value,
                container_id=container.id[:_SHORT_ID_LEN],
                exit_code=exit_code,
                detail=detail,
            )
            msg = f"{stage.value} stage exited with code {exit_code}: {detail}"
            raise FineTuneStageExecutionError(msg)
        logger.info(
            FINE_TUNE_CONTAINER_COMPLETED,
            run_id=run_id,
            stage=stage.value,
            container_id=container.id[:_SHORT_ID_LEN],
        )

    async def _watch_until_exit(  # noqa: PLR0913 -- stage collaborators threaded explicitly
        self,
        container: aiodocker.containers.DockerContainer,
        exit_task: asyncio.Task[int],
        *,
        stage: FineTuneStage,
        execution: FineTuneExecutionConfig,
        run_id: str,
        cancellation: CancellationToken | None,
    ) -> int:
        """Poll for exit while enforcing cancellation and the deadline.

        Returns:
            The container's exit code.

        Raises:
            FineTuneCancelledError: When the cancellation token fired.
            FineTuneStageExecutionError: On timeout.
        """
        deadline = self._clock.monotonic() + execution.timeout_seconds
        # lint-allow: long-running-loop-kill-switch -- bounded supervision:
        # each turn checks cancellation + deadline; ends at container exit.
        while True:
            if cancellation is not None and cancellation.is_cancelled:
                logger.info(
                    FINE_TUNE_CONTAINER_CANCELLED,
                    run_id=run_id,
                    stage=stage.value,
                    container_id=container.id[:_SHORT_ID_LEN],
                )
                await self._stop(container)
                msg = f"{stage.value} stage cancelled"
                raise FineTuneCancelledError(msg)
            if self._clock.monotonic() >= deadline:
                logger.warning(
                    FINE_TUNE_CONTAINER_TIMED_OUT,
                    run_id=run_id,
                    stage=stage.value,
                    container_id=container.id[:_SHORT_ID_LEN],
                    timeout_seconds=execution.timeout_seconds,
                )
                await self._stop(container)
                msg = (
                    f"{stage.value} stage exceeded its"
                    f" {execution.timeout_seconds:.0f}s timeout"
                )
                raise FineTuneStageExecutionError(msg)
            done, _ = await asyncio.wait({exit_task}, timeout=_EXIT_POLL_SECONDS)
            if done:
                return exit_task.result()

    async def _await_exit(
        self,
        container: aiodocker.containers.DockerContainer,
        progress_callback: ProgressCallback | None,
        error_lines: list[str],
    ) -> int:
        """Drain marker output, then return the container's exit code.

        Returns:
            Result of type ``int``.
        """
        async for raw in container.log(stdout=True, stderr=True, follow=True):
            line = raw.strip()
            if line.startswith(_MARKER_PROGRESS):
                if progress_callback is not None:
                    with contextlib.suppress(ValueError):
                        # A malformed fraction is log noise, not a failure.
                        progress_callback(
                            float(line.removeprefix(_MARKER_PROGRESS).strip())
                        )
            elif line.startswith(_MARKER_ERROR):
                error_lines.append(line.removeprefix(_MARKER_ERROR).strip())
        result = await container.wait()
        return int(result.get("StatusCode", -1))

    @staticmethod
    async def _stop(container: aiodocker.containers.DockerContainer) -> None:
        """SIGTERM the container (runner cancels cooperatively), then SIGKILL."""
        try:
            await container.stop(t=_STOP_GRACE_SECONDS)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                FINE_TUNE_CONTAINER_FAILED,
                phase="stop",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    async def _remove(
        container: aiodocker.containers.DockerContainer,
        *,
        run_id: str,
        stage: str,
    ) -> None:
        """Force-remove the container; never leak an ephemeral stage."""
        try:
            await container.delete(force=True)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                FINE_TUNE_CONTAINER_FAILED,
                run_id=run_id,
                stage=stage,
                phase="remove",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _drain_probe_output(
        self, container: aiodocker.containers.DockerContainer
    ) -> str:
        """Collect the probe container's full output until exit.

        Returns:
            Result of type ``str``.
        """
        lines = [
            raw async for raw in container.log(stdout=True, stderr=True, follow=True)
        ]
        await container.wait()
        return "".join(lines)


def _build_host_config(
    execution: FineTuneExecutionConfig,
    data_volume: str,
) -> dict[str, object]:
    """Build the HostConfig for a stage container.

    Returns:
        Result of type ``dict[str, object]``.
    """
    host_config: dict[str, object] = {
        "Binds": [f"{data_volume}:/data:rw"],
        "Memory": parse_memory_limit(execution.memory_limit),
        "PidsLimit": _PIDS_LIMIT,
    }
    if execution.gpu_enabled:
        host_config["DeviceRequests"] = _gpu_device_requests()
    return host_config


def _gpu_device_requests() -> list[dict[str, object]]:
    """Docker DeviceRequests granting all NVIDIA GPUs.

    Returns:
        Result of type ``list[dict[str, object]]``.
    """
    return [
        {
            "Driver": "nvidia",
            "Count": _GPU_COUNT_ALL,
            "Capabilities": [["gpu"]],
        }
    ]
