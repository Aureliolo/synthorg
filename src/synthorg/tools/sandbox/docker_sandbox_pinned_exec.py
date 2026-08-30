# module-kind: complex_service
"""Pinned-foreground-exec mixin for ``DockerSandbox``.

Split out of :mod:`docker_sandbox_exec` to keep that module under its
size cap: the pinned path is one cohesive addition (wrap, drain, kill,
clean up) layered on top of exec primitives three sibling mixins own --
``_open_exec``, ``_collect_exec_output``, ``_safe_close_stream``,
``_exec_returncode`` from :mod:`docker_sandbox_exec`;
``_log_execution_outcome`` and ``_stop_container`` from
:mod:`docker_sandbox_lifecycle`; ``_kill_background_process_group`` and
``_run_control_exec`` from :mod:`docker_sandbox_background` -- declared
here as ``TYPE_CHECKING``-only cross-mixin stubs.

Reached only when the target container already has a live background job
pinning it (``DockerSandboxExecMixin._exec_command`` checks
``BackgroundJobRegistry.has_live_jobs`` before opening the exec); a
container with no live jobs pinning it takes the plain ``_drain_exec``
path instead.
"""

import asyncio
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import aiodocker
from aiodocker.execs import Exec
from aiodocker.stream import Stream

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import DOCKER_EXECUTE_TIMEOUT
from synthorg.observability.events.sandbox import (
    SANDBOX_PINNED_EXEC_CLEANUP_FAILED,
    SANDBOX_PINNED_EXEC_KILL_FAILED,
    SANDBOX_PINNED_EXEC_KILLED,
    SANDBOX_PINNED_EXEC_PID_UNREADABLE,
    SANDBOX_PINNED_EXEC_STARTED,
)
from synthorg.tools.sandbox._background_wrapper import (
    build_pinned_exec_command,
    build_read_pid_command,
    job_dir,
)
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

#: Short attached-exec timeout for the pinned-exec kill path's own
#: control execs (reading back a pidfile, best-effort cleanup): neither
#: does real work, so a large timeout here would only delay surfacing a
#: wedged daemon.
_PINNED_CONTROL_EXEC_TIMEOUT_SECONDS: Final[float] = 10.0

#: Bounds how long the kill path retries an empty pidfile read before
#: accepting it as final. The wrapper writes the pidfile as its very
#: first action (``mkdir -p``; ``echo $$ > pidfile``; ``exec``), but a
#: control exec racing that write -- reached before the daemon has even
#: scheduled the wrapper's shell -- would otherwise read an empty file
#: and stop the whole container for a job that was about to record its
#: pid moments later.
_PID_READ_DEADLINE_SECONDS: Final[float] = 2.0
_PID_READ_RETRY_INTERVAL_SECONDS: Final[float] = 0.1


class DockerSandboxPinnedExecMixin:
    """Foreground exec pinning: killable by process group, container spared."""

    # Attributes + collaborator methods supplied by the concrete
    # DockerSandbox and its sibling mixins.  Declared TYPE_CHECKING-only
    # (signatures, no runtime body) so they exist for the type checker
    # but never shadow the real sibling/concrete implementations in the
    # runtime MRO.
    if TYPE_CHECKING:
        _clock: Clock

        async def _open_exec(
            self,
            docker: aiodocker.Docker,
            handle: ContainerHandle,
            *,
            command: str,
            args: tuple[str, ...],
            container_cwd: str,
            exec_env: dict[str, str],
        ) -> Exec:
            """Create an exec instance in the running container.

            Returns:
                The aiodocker exec instance.
            """
            ...

        @staticmethod
        async def _collect_exec_output(stream: Stream) -> tuple[str, str]:
            """Drain an aiodocker exec stream into ``(stdout, stderr)``.

            Returns:
                The collected ``(stdout, stderr)`` text.
            """
            ...

        @staticmethod
        async def _safe_close_stream(stream: Stream) -> None:
            """Close an exec stream, swallowing any close-time error."""
            ...

        @staticmethod
        async def _exec_returncode(exec_obj: Exec, container_id: str) -> int:
            """Resolve an exec's exit code.

            Returns:
                The exec's exit code.
            """
            ...

        @staticmethod
        def _log_execution_outcome(
            command: str,
            args: tuple[str, ...],
            container_id: str,
            returncode: int,
            stderr: str,
        ) -> None:
            """Log execution outcome."""
            ...

        @staticmethod
        async def _stop_container(
            docker: aiodocker.Docker,
            container_id: str,
        ) -> None:
            """Stop container."""
            ...

        async def _kill_background_process_group(
            self, container_id: NotBlankStr, pid: int
        ) -> None:
            """Kill *pid*'s process group inside *container_id*."""
            ...

        async def _run_control_exec(
            self,
            handle: ContainerHandle,
            program: str,
            args: tuple[str, ...],
            *,
            timeout: float,  # noqa: ASYNC109
        ) -> str:
            """Run a short control exec and return its stdout.

            Returns:
                The exec's captured stdout.
            """
            ...

    async def _drain_exec_pinned(
        self,
        docker: aiodocker.Docker,
        exec_obj: Exec,
        container_id: str,
        timeout: float,  # noqa: ASYNC109
        *,
        pinned_job_id: str,
    ) -> tuple[str, str, bool]:
        """Run a pinned exec stream to completion or timeout.

        Identical to ``DockerSandboxExecMixin._drain_exec`` except its
        timeout branch kills only the timed-out exec's own process group
        instead of stopping the whole container, so a background job
        sharing the container survives.

        Returns:
            ``(stdout, stderr, timed_out)``.
        """
        stream = exec_obj.start(detach=False)
        timed_out = False
        stdout = ""
        stderr = ""
        try:
            stdout, stderr = await asyncio.wait_for(
                self._collect_exec_output(stream),
                timeout=timeout,
            )
        except TimeoutError:
            timed_out = True
            logger.warning(
                DOCKER_EXECUTE_TIMEOUT,
                container_id=container_id[:12],
                timeout=timeout,
            )
            await self._kill_pinned_exec(docker, container_id, pinned_job_id)
        finally:
            await self._safe_close_stream(stream)
        return stdout, stderr, timed_out

    async def _read_pinned_pid(self, handle: ContainerHandle, job_id: str) -> str:
        """Read a pinned exec's recorded pid, retrying while the file is empty.

        Retries within ``_PID_READ_DEADLINE_SECONDS`` rather than
        accepting a single empty read as final -- see that constant's
        own docstring for why an empty read this early is not yet a
        failure. A non-empty read (even unparseable) or a real error
        from the control exec itself both return immediately; only
        "nothing written yet" keeps retrying.

        Returns:
            The pidfile's stripped contents, or ``""`` once the
            deadline elapses with nothing written.
        """
        program, args = build_read_pid_command(job_id)
        deadline = self._clock.monotonic() + _PID_READ_DEADLINE_SECONDS
        # lint-allow: long-running-loop-kill-switch -- bounded by the
        # deadline above (2s); a single foreground kill's own retry.
        while True:
            pid_text = (
                await self._run_control_exec(
                    handle, program, args, timeout=_PINNED_CONTROL_EXEC_TIMEOUT_SECONDS
                )
            ).strip()
            if pid_text or self._clock.monotonic() >= deadline:
                return pid_text
            await self._clock.sleep(_PID_READ_RETRY_INTERVAL_SECONDS)

    async def _kill_pinned_exec(
        self,
        docker: aiodocker.Docker,
        container_id: str,
        job_id: str,
    ) -> None:
        """Kill a pinned exec's own process group, or fall back to the container.

        Reads the pid the wrapped exec recorded via a short control exec.
        A parseable positive pid kills just that process group -- the
        point of pinning: a sibling background job sharing the container
        survives. Falls back to stopping the container -- the honest
        floor -- whenever pinning itself cannot be made to hold: the
        pidfile read fails or times out, its contents are empty or
        unparseable, or the kill exec itself fails. Each branch logs and
        falls back exactly once, so a single failure never produces two
        differently-shaped log entries for the same event.
        """
        handle = ContainerHandle(container_id=container_id)
        try:
            pid_text = await self._read_pinned_pid(handle, job_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SANDBOX_PINNED_EXEC_PID_UNREADABLE,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self._stop_container(docker, container_id)
            return

        try:
            # `str.isdigit()` accepts Unicode digit characters (e.g.
            # superscript "²") that `int()` itself rejects, so a positive
            # `isdigit()` check alone does not guarantee `int()` succeeds.
            pid = int(pid_text)
        except ValueError:
            pid = 0
        if pid <= 0:
            logger.warning(
                SANDBOX_PINNED_EXEC_PID_UNREADABLE,
                container_id=container_id[:12],
                pid_text=pid_text,
            )
            await self._stop_container(docker, container_id)
            return
        try:
            await self._kill_background_process_group(NotBlankStr(container_id), pid)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SANDBOX_PINNED_EXEC_KILL_FAILED,
                container_id=container_id[:12],
                pid=pid,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self._stop_container(docker, container_id)
            return

        logger.info(
            SANDBOX_PINNED_EXEC_KILLED,
            container_id=container_id[:12],
            pid=pid,
        )

    async def _cleanup_pinned_job_dir(self, container_id: str, job_id: str) -> None:
        """Best-effort removal of a pinned exec's own scratch directory.

        Never durable: nothing depends on this file surviving. Without
        it, a long-lived pinned container would accumulate one pidfile
        directory per foreground call for its whole lifetime.
        """
        handle = ContainerHandle(container_id=container_id)
        try:
            await self._run_control_exec(
                handle,
                "rm",
                ("-rf", job_dir(job_id)),
                timeout=_PINNED_CONTROL_EXEC_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                SANDBOX_PINNED_EXEC_CLEANUP_FAILED,
                container_id=container_id[:12],
                job_id=job_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _finish_exec_result(
        self,
        *,
        exec_obj: Exec,
        command: str,
        args: tuple[str, ...],
        container_id: str,
        drained: tuple[str, str, bool],
        timeout: float,  # noqa: ASYNC109
        elapsed_ms: int,
    ) -> SandboxResult:
        """Resolve the exit code, log the outcome, and build the result.

        Shared tail for both the ordinary and pinned exec paths, so the
        two cannot silently drift in what a caller receives back.

        Args:
            exec_obj: The completed (or timed-out) exec instance.
            command: Executable name or path that was run.
            args: Its arguments.
            container_id: The container the exec ran in.
            drained: ``(stdout, stderr, timed_out)`` from
                ``DockerSandboxExecMixin._drain_exec`` or
                :meth:`_drain_exec_pinned`.
            timeout: Seconds the caller allowed before killing.
            elapsed_ms: Wall-clock milliseconds the exec took.

        Returns:
            A ``SandboxResult`` with captured output and exit status.
        """
        stdout, stderr, timed_out = drained
        if timed_out:
            returncode = -1
        else:
            returncode = await self._exec_returncode(exec_obj, container_id)

        self._log_execution_outcome(
            command,
            args,
            container_id,
            returncode,
            stderr,
        )

        if timed_out:
            return SandboxResult(
                stdout=stdout,
                stderr=stderr or f"Command timed out after {timeout}s",
                returncode=returncode,
                timed_out=True,
                container_id=container_id,
                execution_time_ms=elapsed_ms,
            )
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            container_id=container_id,
            execution_time_ms=elapsed_ms,
        )

    async def _exec_command_pinned(
        self,
        *,
        docker: aiodocker.Docker,
        handle: ContainerHandle,
        command: str,
        args: tuple[str, ...],
        container_cwd: str,
        exec_env: dict[str, str],
        timeout: float,  # noqa: ASYNC109
    ) -> SandboxResult:
        """Run *command* pinned: killable by process group, container spared.

        Reached only when the container already has a live background
        job pinning it (see ``DockerSandboxExecMixin._exec_command``).
        Streams stdout/stderr exactly like an ordinary foreground exec
        (see ``build_pinned_exec_command``); only the exec'd script and
        the timeout branch's kill target differ.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the exec instance cannot be created.
        """
        container_id = handle.container_id
        job_id = str(uuid4())
        logger.debug(
            SANDBOX_PINNED_EXEC_STARTED,
            container_id=container_id[:12],
            job_id=job_id,
        )
        program, wrapped_args = build_pinned_exec_command(job_id, command, args)
        exec_obj = await self._open_exec(
            docker,
            handle,
            command=program,
            args=wrapped_args,
            container_cwd=container_cwd,
            exec_env=exec_env,
        )
        start_mono = self._clock.monotonic()
        try:
            stdout, stderr, timed_out = await self._drain_exec_pinned(
                docker,
                exec_obj,
                container_id,
                timeout,
                pinned_job_id=job_id,
            )
            elapsed_ms = int((self._clock.monotonic() - start_mono) * 1000)
        finally:
            await self._cleanup_pinned_job_dir(container_id, job_id)

        return await self._finish_exec_result(
            exec_obj=exec_obj,
            command=command,
            args=args,
            container_id=container_id,
            drained=(stdout, stderr, timed_out),
            timeout=timeout,
            elapsed_ms=elapsed_ms,
        )


__all__ = ["DockerSandboxPinnedExecMixin"]
