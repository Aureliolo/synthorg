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
import json
from typing import Final, cast

import aiodocker
import aiodocker.containers
from aiodocker.types import JSONObject

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage, ProgressCallback
from synthorg.memory.embedding.fine_tune_container_config import (
    PROBE_CACHE_DIR,
    STAGE_CACHE_DIR,
    build_probe_host_config,
    build_stage_host_config,
    cache_env,
)
from synthorg.memory.embedding.fine_tune_container_logs import (
    drain_probe_output,
    stream_markers_until_exit,
)
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.embedding.fine_tune_probe_result import (
    ProbeResult,
    parse_probe_output,
)
from synthorg.memory.errors import FineTuneCancelledError, FineTuneStageExecutionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.fine_tune import (
    FINE_TUNE_CONTAINER_CANCELLED,
    FINE_TUNE_CONTAINER_COMPLETED,
    FINE_TUNE_CONTAINER_FAILED,
    FINE_TUNE_CONTAINER_STARTED,
    FINE_TUNE_CONTAINER_TIMED_OUT,
    FINE_TUNE_DOCKER_CONNECT_RETRIED,
    FINE_TUNE_PROBE_FAILED,
    FINE_TUNE_PROBE_STARTED,
)

logger = get_logger(__name__)

STAGE_CONFIG_ENV: Final[str] = "SYNTHORG_FINE_TUNE_STAGE_CONFIG"
PROBE_ENV: Final[str] = "SYNTHORG_FINE_TUNE_PROBE"

# Poll cadence for the cancellation watcher between exit checks.
_EXIT_POLL_SECONDS: Final[float] = 0.5
# Grace Docker gives the runner's SIGTERM handler before SIGKILL.
_STOP_GRACE_SECONDS: Final[int] = 10
_PROBE_TIMEOUT_SECONDS: Final[float] = 120.0
_SHORT_ID_LEN: Final[int] = 12
# Ceiling on individual Docker API calls (version/create/start) so a
# hung daemon can never wedge a run below the stage deadline.
_DOCKER_API_TIMEOUT_SECONDS: Final[float] = 30.0
_CONNECT_RETRY_ATTEMPTS: Final[int] = 3
_CONNECT_RETRY_BASE_SECONDS: Final[float] = 0.5
_CONNECT_RETRY_CAP_SECONDS: Final[float] = 2.0

LABEL_MANAGED: Final[str] = "synthorg.managed"
LABEL_COMPONENT: Final[str] = "synthorg.component"
COMPONENT_FINE_TUNE: Final[str] = "fine-tune"
LABEL_RUN_ID: Final[str] = "synthorg.fine_tune.run_id"
LABEL_STAGE: Final[str] = "synthorg.fine_tune.stage"


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
        container_config: dict[str, object] = {
            "Image": execution.image,
            "Env": [
                f"{STAGE_CONFIG_ENV}={json.dumps(config)}",
                *cache_env(STAGE_CACHE_DIR),
            ],
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_COMPONENT: COMPONENT_FINE_TUNE,
                LABEL_RUN_ID: run_id,
                LABEL_STAGE: stage.value,
            },
            "HostConfig": build_stage_host_config(execution, data_volume),
        }
        container = await self._create_and_start(
            docker,
            container_config,
            run_id=run_id,
            stage=stage.value,
            image=execution.image,
        )
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

    async def _create_and_start(
        self,
        docker: aiodocker.Docker,
        container_config: dict[str, object],
        *,
        run_id: str,
        stage: str,
        image: str | None,
    ) -> aiodocker.containers.DockerContainer:
        """Create and start one container, never leaking a created one.

        Returns:
            The running container.

        Raises:
            FineTuneStageExecutionError: On a create/start failure or a
                Docker API call exceeding its ceiling; a container that
                was created but failed to start is removed first.
        """
        try:
            container = await asyncio.wait_for(
                docker.containers.create(config=cast("JSONObject", container_config)),
                timeout=_DOCKER_API_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            reraise_critical(exc)
            raise self._launch_failure(
                exc, run_id=run_id, stage=stage, image=image
            ) from exc
        try:
            await asyncio.wait_for(
                container.start(), timeout=_DOCKER_API_TIMEOUT_SECONDS
            )
        except Exception as exc:
            reraise_critical(exc)
            await self._remove(container, run_id=run_id, stage=stage)
            raise self._launch_failure(
                exc, run_id=run_id, stage=stage, image=image
            ) from exc
        return container

    @staticmethod
    def _launch_failure(
        exc: Exception,
        *,
        run_id: str,
        stage: str,
        image: str | None,
    ) -> FineTuneStageExecutionError:
        """Log a launch failure and build its typed error.

        Returns:
            The error for the caller to raise (keeps the raise at the
            call site so the traceback points at the failing phase).
        """
        logger.warning(
            FINE_TUNE_CONTAINER_FAILED,
            run_id=run_id,
            stage=stage,
            phase="launch",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"could not launch the {stage} stage container"
            f" (image {image}): {safe_error_description(exc)}"
        )
        return FineTuneStageExecutionError(msg)

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
            detail = str(exc)
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, detail=detail)
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
        container_config: dict[str, object] = {
            "Image": image,
            "Env": [f"{PROBE_ENV}=1", *cache_env(PROBE_CACHE_DIR)],
            "Labels": {
                LABEL_MANAGED: "true",
                LABEL_COMPONENT: COMPONENT_FINE_TUNE,
                LABEL_STAGE: "probe",
            },
            "HostConfig": build_probe_host_config(gpu_enabled=gpu_enabled),
        }
        try:
            container = await self._create_and_start(
                docker, container_config, run_id="probe", stage="probe", image=image
            )
        except FineTuneStageExecutionError as exc:
            detail = str(exc)
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, detail=detail)
        try:
            output = await asyncio.wait_for(
                drain_probe_output(container),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            detail = f"probe timed out after {_PROBE_TIMEOUT_SECONDS:.0f}s"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, detail=detail)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            detail = f"probe failed to run: {safe_error_description(exc)}"
            logger.warning(FINE_TUNE_PROBE_FAILED, image=image, detail=detail)
            return ProbeResult(ok=False, detail=detail)
        finally:
            await self._remove(container, run_id="probe", stage="probe")
        return parse_probe_output(output, image=image)

    # -- Internals ------------------------------------------------------

    @staticmethod
    async def _connect() -> aiodocker.Docker:
        """Open a Docker client and verify the daemon answers.

        The version probe gets a bounded retry (a momentary daemon blip
        must not kill a multi-hour run outright) and a per-call ceiling
        (a hung daemon must not wedge the caller forever).

        Returns:
            An ``aiodocker.Docker`` client the caller must close.

        Raises:
            FineTuneStageExecutionError: If the daemon is unavailable.
        """
        client = aiodocker.Docker()
        retry = GeneralRetryHandler(
            retryable=_is_retryable_daemon_error,
            max_attempts=_CONNECT_RETRY_ATTEMPTS,
            base=_CONNECT_RETRY_BASE_SECONDS,
            cap=_CONNECT_RETRY_CAP_SECONDS,
            event=FINE_TUNE_DOCKER_CONNECT_RETRIED,
        )
        try:
            await retry.execute(
                lambda: asyncio.wait_for(
                    client.version(), timeout=_DOCKER_API_TIMEOUT_SECONDS
                )
            )
        except Exception as exc:
            reraise_critical(exc)
            await client.close()
            logger.warning(
                FINE_TUNE_CONTAINER_FAILED,
                phase="connect",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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
            stream_markers_until_exit(container, progress_callback, error_lines),
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
            # Absorb the reaped task's outcome unconditionally (a task
            # that already finished with an exception would otherwise
            # surface as "exception was never retrieved" noise) so it
            # cannot mask the in-flight cancellation/timeout error.
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

    @staticmethod
    async def _stop(container: aiodocker.containers.DockerContainer) -> None:
        """SIGTERM the container (runner cancels cooperatively), then SIGKILL.

        ``t=`` only bounds the daemon's graceful-shutdown window, not the
        HTTP request itself, so the call carries its own client-side
        ceiling (grace window plus the per-call API budget); a stalled
        daemon degrades to a logged warning instead of a hung cleanup,
        and the reconciliation sweep reaps anything left behind.
        """
        try:
            await asyncio.wait_for(
                container.stop(t=_STOP_GRACE_SECONDS),
                timeout=_STOP_GRACE_SECONDS + _DOCKER_API_TIMEOUT_SECONDS,
            )
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
        """Force-remove the container; never leak an ephemeral stage.

        Client-side ceiling for the same reason as ``_stop``: a stalled
        daemon must not wedge the ``finally`` path of a run, and the
        reconciliation sweep covers a container the timeout abandoned.
        """
        try:
            await asyncio.wait_for(
                container.delete(force=True),
                timeout=_DOCKER_API_TIMEOUT_SECONDS,
            )
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


def _is_retryable_daemon_error(exc: Exception) -> bool:
    """Whether a daemon version-probe failure is worth another attempt.

    Returns:
        ``True`` for everything except critical exhaustion errors.
    """
    return not isinstance(exc, MemoryError | RecursionError)
