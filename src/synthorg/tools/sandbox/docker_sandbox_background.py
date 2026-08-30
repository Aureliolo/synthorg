# module-kind: complex_service
"""Background-job start/poll/read/cancel mixin for ``DockerSandbox``.

New territory rather than an addition to ``docker_sandbox.py`` or
``docker_sandbox_exec.py``: both already sit at their module-size
budget, so every genuinely new responsibility this feature needs lives
here instead.

Owns the same setup sequence ``DockerSandboxExecMixin._execute_leased``
runs (project root -> cwd resolution -> env resolution -> lifecycle
owner key -> container acquisition) up to the point a foreground
``execute()`` would run its command and tear the container back down;
a background job never reaches that teardown; the container is left
running, pinned open by the job's own live tracking row via the
lifecycle strategy's ``pin_check`` (see ``lifecycle/per_agent.py`` and
``lifecycle/per_task.py``).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import aiodocker

from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_BACKGROUND_JOB_CANCELLED,
    SANDBOX_BACKGROUND_JOB_START_FAILED,
    SANDBOX_BACKGROUND_JOB_STARTED,
)
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.tools.sandbox._background_wrapper import (
    build_kill_command,
    build_liveness_command,
    build_read_output_command,
    build_start_command,
    output_path,
)
from synthorg.tools.sandbox._mount_mode import resolve_mount_mode
from synthorg.tools.sandbox._mount_paths import CONTAINER_TMP
from synthorg.tools.sandbox._owner_key import context_project
from synthorg.tools.sandbox.active_environment import get_active_sandbox_environment
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.errors import (
    SandboxBackgroundJobLimitError,
    SandboxBackgroundJobNotFoundError,
    SandboxBackgroundNoReusableContainerError,
    SandboxBackgroundUnsupportedError,
    SandboxStartError,
)
from synthorg.tools.sandbox.lifecycle.protocol import (
    ContainerHandle,
    SandboxLifecycleStrategy,
)

if TYPE_CHECKING:
    from aiodocker.execs import Exec

logger = get_logger(__name__)

#: The wrapper's own start-confirmation poll caps out at
#: ``_PID_POLL_MAX_ITERATIONS * _PID_POLL_INTERVAL_SECONDS`` == 10s
#: (see ``_background_wrapper.py``); this exec's own timeout must sit
#: comfortably above that so a slow-but-legitimate confirmation is
#: never killed by the exec's own watchdog before the wrapper's own
#: bounded loop would have given up and returned on its own.
_START_EXEC_TIMEOUT_SECONDS: Final[float] = 20.0

#: Grace between TERM and KILL when cancelling a background job.
_CANCEL_GRACE_SECONDS: Final[float] = 5.0

#: Truncation length for the observability-only ``command_repr`` field
#: (surfaced by ``list_background_jobs``, never the full untruncated
#: command). Mirrors ``shell_command.py``'s own ``_COMMAND_REPR_LIMIT``
#: in shape, sized independently since this one is read back by an
#: agent rather than only logged.
_COMMAND_REPR_LIMIT: Final[int] = 200

#: Short attached-exec timeout for poll/read/cancel/liveness calls,
#: none of which do real work themselves (they read a file or signal a
#: process); a large timeout here would only delay surfacing a wedged
#: daemon.
_CONTROL_EXEC_TIMEOUT_SECONDS: Final[float] = 10.0

#: Fallback per-job duration ceiling when a caller supplies none to
#: ``start_background`` (e.g. a direct call bypassing the tool layer).
#: The tool layer itself always resolves and passes the live
#: ``tools.shell_command_background_max_duration_seconds`` value
#: (Shape A -- read per call, since the ceiling in force when a job
#: starts governs its own lifetime). The per-owner concurrent-job
#: ceiling and the output byte cap are Shape B instead (resolved once
#: at construction into ``self._background_max_concurrent_jobs`` /
#: ``self._background_output_byte_cap``, via ``ToolCeilings``), since
#: they bound registry/tmpfs capacity rather than one call's behaviour.
_DEFAULT_MAX_DURATION_SECONDS: Final[float] = 3600.0


class DockerSandboxBackgroundMixin:
    """Start, poll, read, and cancel a detached job in the keep-alive container."""

    # Attributes + collaborator methods supplied by the concrete
    # DockerSandbox and its sibling mixins. See DockerSandboxExecMixin's
    # own TYPE_CHECKING block for the rationale (never shadows the real
    # runtime implementations; exists only for the type checker).
    if TYPE_CHECKING:
        _config: DockerSandboxConfig
        _clock: Clock
        _docker: aiodocker.Docker | None
        _lifecycle_strategy: SandboxLifecycleStrategy
        _background_jobs: BackgroundJobRegistry | None
        _background_max_concurrent_jobs: int
        _background_output_byte_cap: int
        _background_job_locks: dict[str, asyncio.Lock]

        async def _ensure_docker(self) -> aiodocker.Docker: ...

        async def _project_root(self, project_id: str | None) -> Path: ...

        @staticmethod
        def _rooted(cwd: Path, effective_root: Path) -> Path: ...

        def _validate_cwd(
            self, cwd: Path, effective_root: Path | None = None
        ) -> None: ...

        def _resolve_cwd_in_container(
            self, cwd: Path | None, effective_root: Path | None = None
        ) -> str: ...

        def _resolve_exec_env(
            self, env_overrides: Mapping[str, str] | None
        ) -> dict[str, str]: ...

        def _screen_declaration_env(
            self, env_additions: Mapping[str, str]
        ) -> dict[str, str]: ...

        def _resolve_lifecycle(
            self,
            owner_id: str | None,
            *,
            project_id: str | None = None,
            image_override: str | None = None,
            mount_mode: object | None = None,
        ) -> tuple[str, bool]: ...

        async def _acquire_owner_handle(
            self,
            *,
            owner_key: str,
            strategy_owns: bool,
            create_fn: Callable[[], Awaitable[ContainerHandle]],
        ) -> ContainerHandle: ...

        async def _create_keepalive_handle(
            self,
            *,
            docker: aiodocker.Docker,
            container_cwd: str,
            env_overrides: Mapping[str, str] | None,
            effective_root: Path,
            category: str,
            owner_label: str,
            image_override: NotBlankStr | None = None,
        ) -> ContainerHandle: ...

        async def _open_exec(
            self,
            docker: aiodocker.Docker,
            handle: ContainerHandle,
            *,
            command: str,
            args: tuple[str, ...],
            container_cwd: str,
            exec_env: dict[str, str],
        ) -> Exec: ...

        async def _drain_exec(
            self,
            docker: aiodocker.Docker,
            exec_obj: Exec,
            container_id: str,
            timeout: float,  # noqa: ASYNC109
        ) -> tuple[str, str, bool]: ...

    def _require_background_jobs(self) -> BackgroundJobRegistry:
        """Return the wired registry, or refuse when none was attached.

        Returns:
            The attached :class:`BackgroundJobRegistry`.

        Raises:
            SandboxBackgroundUnsupportedError: No registry was wired at
                construction (``background_jobs=None``); the feature is
                off for this backend instance.
        """
        if self._background_jobs is None:
            msg = "Background shell commands are not wired for this sandbox backend."
            raise SandboxBackgroundUnsupportedError(msg)
        return self._background_jobs

    def _owner_lock(self, owner_key: str) -> asyncio.Lock:
        """Return the per-owner lock guarding the job-cap check + persist.

        Created on first use and kept for the process lifetime, mirroring
        ``_tracked_containers``'s own unbounded-but-roster-bounded growth.
        No ``await`` runs between the ``get`` and the ``setdefault``
        below, so this is race-free without its own lock: an interleaved
        coroutine cannot observe the dict mid-update.

        Returns:
            The owner's lock, creating one if this is its first use.
        """
        lock = self._background_job_locks.get(owner_key)
        if lock is None:
            lock = asyncio.Lock()
            self._background_job_locks[owner_key] = lock
        return lock

    async def _run_control_exec(
        self,
        handle: ContainerHandle,
        program: str,
        args: tuple[str, ...],
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> str:
        """Run a wrapper-builder command and return its stdout.

        Returns:
            The exec's captured stdout.

        Raises:
            SandboxStartError: The exec could not be opened, or timed
                out.
        """
        docker = await self._ensure_docker()
        exec_obj = await self._open_exec(
            docker,
            handle,
            command=program,
            args=args,
            container_cwd=CONTAINER_TMP,
            exec_env={},
        )
        stdout, _stderr, timed_out = await self._drain_exec(
            docker, exec_obj, handle.container_id, timeout
        )
        if timed_out:
            msg = (
                f"Background-job control command timed out after "
                f"{timeout}s against container {handle.container_id[:12]}"
            )
            raise SandboxStartError(msg)
        return stdout

    async def _kill_background_process_group(
        self, container_id: NotBlankStr, pid: int
    ) -> None:
        """Kill *pid*'s process group inside *container_id*.

        The shared kill primitive: used by :meth:`cancel_background`,
        the lifecycle strategy's own pin-check self-cleaning
        (:meth:`pin_check`), and (a later phase) the foreground-exec
        timeout fix.
        """
        program, args = build_kill_command(pid, grace_seconds=_CANCEL_GRACE_SECONDS)
        handle = ContainerHandle(container_id=container_id)
        await self._run_control_exec(
            handle, program, args, timeout=_CONTROL_EXEC_TIMEOUT_SECONDS
        )

    async def pin_check(self, container_id: NotBlankStr) -> bool:
        """Answer whether *container_id* has live background jobs.

        Self-cleaning predicate. Not a private implementation
        detail: this bound method is itself the ``pin_check`` callable
        that boot wiring hands to ``create_lifecycle_strategy`` once
        this sandbox exists, so the reusable lifecycle strategies
        (``per-agent`` / ``per-task``) hold container teardown off
        while a job it names is still running. Force-cancels (kill,
        then mark ``TIMED_OUT``) any job past its own
        ``max_duration_seconds`` first, then reports whether anything
        genuinely live remains. Reuses the strategy's own grace/idle
        recheck cadence rather than a second polling loop.

        Returns:
            ``True`` while at least one live job remains after expiry.
        """
        registry = self._background_jobs
        if registry is None:
            return False
        still_live = await registry.expire_overdue(
            container_id, kill_fn=self._kill_background_process_group
        )
        return len(still_live) > 0

    def _resolve_background_owner_key(
        self,
        owner_id: str | None,
        *,
        project_id: str | None,
        category: str,
    ) -> tuple[str, bool]:
        """Resolve the lifecycle owner key a background job is filed under.

        Container-free (no acquisition, no I/O): the same resolution
        ``_resolve_background_target`` performs before acquiring a
        container, split out so ``list_background_jobs`` can ask "which
        rows are mine" without paying for a container acquisition, and
        so both call sites are guaranteed to agree on the key --
        including its ``mount_mode`` segment, which ``execute()`` /
        ``_execute_leased`` also fold in via ``resolve_mount_mode``.
        Omitting that segment here would key a background job's rows
        under the unqualified owner while ``execute()`` keys the SAME
        owner's foreground container under the mount-mode-suffixed
        form, so a job would silently pin a container the agent's own
        foreground calls never use.

        Returns:
            ``(owner_key, strategy_owns)`` -- ``strategy_owns`` is
            ``False`` when nothing derivable meant the key degraded to
            an ephemeral one-off (see ``resolve_lifecycle``).
        """
        pid = str(project_id) if project_id is not None else context_project()
        active_env = get_active_sandbox_environment()
        image_override = active_env.image_override if active_env is not None else None
        return self._resolve_lifecycle(
            owner_id,
            project_id=pid,
            image_override=str(image_override) if image_override else None,
            mount_mode=resolve_mount_mode(category, self._config.mount_mode),
        )

    async def _resolve_background_target(
        self,
        *,
        cwd: Path | None,
        env_overrides: Mapping[str, str] | None,
        category: str,
        owner_id: str | None,
        project_id: NotBlankStr | None,
    ) -> tuple[aiodocker.Docker, ContainerHandle, str, dict[str, str], str]:
        """Resolve + acquire the container a background job runs in.

        Mirrors ``DockerSandboxExecMixin._execute_leased``'s setup
        sequence up to (not including) running a command: project root,
        rooted/validated cwd, container-side cwd, resolved env, and the
        acquired keep-alive container handle for the resolved owner.
        The caller (``start_background``) has already resolved and
        checked ``owner_key`` / ``strategy_owns`` once via
        ``_resolve_background_owner_key`` before reaching here; this
        resolves them again (cheap -- pure string computation, no I/O)
        rather than threading them through as extra parameters.

        Returns:
            ``(docker, handle, container_cwd, exec_env, owner_key)``.

        Raises:
            SandboxError: *cwd* is outside the workspace boundary, or
                *env_overrides* set a reserved variable.
            SandboxStartError: The Docker daemon or image is
                unavailable.
        """
        owner_key, strategy_owns = self._resolve_background_owner_key(
            owner_id, project_id=project_id, category=category
        )
        pid = str(project_id) if project_id is not None else context_project()
        effective_root = await self._project_root(pid)
        rooted_cwd = None if cwd is None else self._rooted(cwd, effective_root)
        work_dir = rooted_cwd if rooted_cwd is not None else effective_root
        self._validate_cwd(work_dir, effective_root)
        container_cwd = self._resolve_cwd_in_container(rooted_cwd, effective_root)

        active_env = get_active_sandbox_environment()
        image_override = active_env.image_override if active_env is not None else None
        effective_overrides: Mapping[str, str] | None = env_overrides
        if active_env is not None and active_env.env_additions:
            screened = self._screen_declaration_env(active_env.env_additions)
            effective_overrides = {**screened, **(env_overrides or {})}
        exec_env = self._resolve_exec_env(effective_overrides)

        docker = await self._ensure_docker()

        async def _create() -> ContainerHandle:
            return await self._create_keepalive_handle(
                docker=docker,
                container_cwd=container_cwd,
                env_overrides=effective_overrides,
                effective_root=effective_root,
                category=category,
                owner_label=owner_key,
                image_override=image_override,
            )

        handle = await self._acquire_owner_handle(
            owner_key=owner_key,
            strategy_owns=strategy_owns,
            create_fn=_create,
        )
        return docker, handle, container_cwd, exec_env, owner_key

    async def start_background(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
        max_duration_seconds: float | None = None,
    ) -> NotBlankStr:
        """See ``SandboxBackend.start_background``.

        Returns:
            The started job's id.

        Raises:
            SandboxBackgroundUnsupportedError: No registry was wired.
            SandboxBackgroundNoReusableContainerError: The resolved
                lifecycle strategy has no persistent container, or no
                owner could be derived for one that does (an explicit
                *owner_id* failed format validation, or ``None`` and
                nothing was in the correlation context) -- in both
                cases resolution degraded to an ephemeral one-off key,
                which a background job cannot pin.
            SandboxBackgroundJobLimitError: The resolved owner already
                holds the maximum number of live background jobs.
            SandboxStartError: The job could not be confirmed started
                (covers a shell-level failure -- a read-only ``/tmp``,
                an exhausted tmpfs -- surfacing as empty or non-numeric
                stdout after the wrapper's own bounded wait; never
                persisted as a job record, since there would be no real
                job for any caller to poll).
        """
        registry = self._require_background_jobs()
        if not self._lifecycle_strategy.reuses_container:
            msg = (
                "The configured sandbox lifecycle strategy destroys its "
                "container after every command."
            )
            raise SandboxBackgroundNoReusableContainerError(msg)

        # Resolved once, up front: this is the SAME key the container
        # acquisition below will resolve again (cheap, no I/O) and the
        # SAME key ``execute()`` resolves for this owner's foreground
        # calls under the same category -- the cap check and the
        # persisted record must both key on it, never on the raw
        # *owner_id* argument, or the two never agree (a raw owner_id
        # has no project/image/mount-mode suffix; a resolved key does).
        owner_key, strategy_owns = self._resolve_background_owner_key(
            owner_id, project_id=project_id, category=category
        )
        if not strategy_owns:
            msg = (
                "No background-job owner could be resolved: an explicit "
                "owner_id failed format validation, or none was given and "
                "nothing usable was in the correlation context, so there "
                "is no reusable container for a background job to pin."
            )
            raise SandboxBackgroundNoReusableContainerError(msg)

        # Held from the cap check through the persisted save: without it,
        # two concurrent start_background calls for the same owner can
        # each read a count under the ceiling and both persist, since
        # nothing else serialises the check against the write.
        async with self._owner_lock(owner_key):
            live_count = await registry.count_live_by_owner(owner_key)
            if live_count >= self._background_max_concurrent_jobs:
                msg = (
                    f"{owner_key} already holds {live_count} live "
                    f"background job(s), at the "
                    f"{self._background_max_concurrent_jobs}-job ceiling."
                )
                raise SandboxBackgroundJobLimitError(msg)

            (
                docker,
                handle,
                container_cwd,
                exec_env,
                owner_key,
            ) = await self._resolve_background_target(
                cwd=cwd,
                env_overrides=env_overrides,
                category=category,
                owner_id=owner_id,
                project_id=project_id,
            )

            job_id = NotBlankStr(str(uuid4()))
            full_command = " ".join((command, *args)) if args else command
            program, wrapper_args = build_start_command(
                job_id, full_command, container_cwd=container_cwd
            )
            exec_obj = await self._open_exec(
                docker,
                handle,
                command=program,
                args=wrapper_args,
                container_cwd=container_cwd,
                exec_env=exec_env,
            )
            stdout, stderr, timed_out = await self._drain_exec(
                docker, exec_obj, handle.container_id, _START_EXEC_TIMEOUT_SECONDS
            )
            pid_text = stdout.strip()
            if timed_out or not pid_text.isdigit():
                logger.warning(
                    SANDBOX_BACKGROUND_JOB_START_FAILED,
                    job_id=job_id,
                    container_id=handle.container_id[:12],
                    owner_id=owner_key,
                    timed_out=timed_out,
                    stderr=safe_error_description(SandboxStartError(stderr))
                    if stderr
                    else "",
                )
                msg = (
                    f"Background job failed to start: the wrapper never "
                    f"confirmed a pid (stdout={pid_text!r})"
                )
                raise SandboxStartError(msg)

            now = self._clock.now()
            record = BackgroundJobRecord(
                job_id=job_id,
                container_id=NotBlankStr(handle.container_id),
                owner_id=owner_key,
                project_id=project_id,
                command_repr=full_command[:_COMMAND_REPR_LIMIT],
                pid=int(pid_text),
                status=BackgroundJobStatus.RUNNING,
                output_path=output_path(job_id),
                started_at=now,
                updated_at=now,
                max_duration_seconds=(
                    max_duration_seconds
                    if max_duration_seconds is not None and max_duration_seconds > 0
                    else _DEFAULT_MAX_DURATION_SECONDS
                ),
            )
            await registry.save(record)
        logger.info(
            SANDBOX_BACKGROUND_JOB_STARTED,
            job_id=job_id,
            container_id=handle.container_id[:12],
            owner_id=owner_key,
            pid=record.pid,
        )
        return job_id

    async def _refresh_if_live(
        self, record: BackgroundJobRecord
    ) -> BackgroundJobRecord:
        """Probe a still-live job's exit-code sentinel and update its row.

        Returns:
            The (possibly updated) record.
        """
        if record.status not in (
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RUNNING,
        ):
            return record
        registry = self._require_background_jobs()
        handle = ContainerHandle(container_id=record.container_id)
        program, args = build_liveness_command(record.job_id)
        stdout = await self._run_control_exec(
            handle, program, args, timeout=_CONTROL_EXEC_TIMEOUT_SECONDS
        )
        status_text = stdout.strip()
        if status_text == "RUNNING" or not status_text:
            return record
        try:
            exit_code = int(status_text)
        except ValueError:
            return record
        new_status = (
            BackgroundJobStatus.COMPLETED
            if exit_code == 0
            else BackgroundJobStatus.FAILED
        )
        return await registry.mark_terminal(record, new_status, exit_code=exit_code)

    async def _get_owned_job(
        self,
        job_id: NotBlankStr,
        *,
        category: str,
        owner_id: str | None,
        project_id: NotBlankStr | None,
    ) -> BackgroundJobRecord:
        """Return *job_id*'s record, refusing one owned by a different caller.

        Every job-targeted method (poll/read/cancel) routes through
        here rather than a bare ``registry.get(job_id)``: without this,
        knowing another owner's ``job_id`` (from ``list_background_jobs``,
        a shared task, or a log line) was enough to read or cancel their
        job, crossing both agent and project boundaries. The not-found
        error deliberately covers both "no such job" and "not yours" --
        distinguishing them would let a caller enumerate another
        owner's job ids by probing.

        Returns:
            The job's persisted record.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*,
                or it belongs to a different resolved owner.
        """
        registry = self._require_background_jobs()
        record = await registry.get(job_id)
        caller_key, strategy_owns = self._resolve_background_owner_key(
            owner_id, project_id=project_id, category=category
        )
        if record is None or not strategy_owns or record.owner_id != caller_key:
            msg = f"No background job matches {job_id!r}"
            raise SandboxBackgroundJobNotFoundError(msg)
        return record

    async def poll_background(
        self,
        job_id: NotBlankStr,
        *,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> BackgroundJobRecord:
        """See ``SandboxBackend.poll_background``.

        Returns:
            The job's current tracking row.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*
                under the caller's resolved owner key.
        """
        record = await self._get_owned_job(
            job_id, category=category, owner_id=owner_id, project_id=project_id
        )
        return await self._refresh_if_live(record)

    async def read_background_output(
        self,
        job_id: NotBlankStr,
        *,
        byte_cap: int,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> str:
        """See ``SandboxBackend.read_background_output``.

        Returns:
            The captured output, truncated to *byte_cap* bytes.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*
                under the caller's resolved owner key.
        """
        record = await self._get_owned_job(
            job_id, category=category, owner_id=owner_id, project_id=project_id
        )
        handle = ContainerHandle(container_id=record.container_id)
        # Clamped, never merely defaulted: a caller-supplied byte_cap
        # above the operator's configured ceiling would otherwise make
        # `shell_command_background_output_byte_cap` dead on this path
        # (a positive caller value is never falsy, so an `or` fallback
        # only ever applied to the caller's own 0, which the tool layer
        # already rejects before reaching here).
        effective_cap = (
            min(byte_cap, self._background_output_byte_cap)
            if byte_cap > 0
            else self._background_output_byte_cap
        )
        program, args = build_read_output_command(job_id, byte_cap=effective_cap)
        return await self._run_control_exec(
            handle, program, args, timeout=_CONTROL_EXEC_TIMEOUT_SECONDS
        )

    async def cancel_background(
        self,
        job_id: NotBlankStr,
        *,
        category: str = "",
        owner_id: NotBlankStr | None = None,
        project_id: NotBlankStr | None = None,
    ) -> BackgroundJobRecord:
        """See ``SandboxBackend.cancel_background``.

        Returns:
            The job's tracking row after cancellation.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*
                under the caller's resolved owner key.
        """
        record = await self._get_owned_job(
            job_id, category=category, owner_id=owner_id, project_id=project_id
        )
        record = await self._refresh_if_live(record)
        if record.status not in (
            BackgroundJobStatus.PENDING,
            BackgroundJobStatus.RUNNING,
        ):
            return record
        if record.pid is not None:
            await self._kill_background_process_group(record.container_id, record.pid)
        registry = self._require_background_jobs()
        updated = await registry.mark_terminal(record, BackgroundJobStatus.CANCELLED)
        logger.info(
            SANDBOX_BACKGROUND_JOB_CANCELLED,
            job_id=job_id,
            container_id=record.container_id[:12],
        )
        return updated

    async def list_background_jobs(
        self,
        owner_id: NotBlankStr | None = None,
        *,
        category: str = "",
        project_id: NotBlankStr | None = None,
    ) -> tuple[BackgroundJobRecord, ...]:
        """See ``SandboxBackend.list_background_jobs``.

        Resolves *owner_id* through the same
        ``_resolve_background_owner_key`` path ``start_background``
        persisted rows under, rather than querying the raw argument
        directly: a raw agent/task id has no project/image/mount-mode
        suffix, so it never matches a persisted (resolved) key.

        Returns:
            The resolved owner's job rows, newest-first. Empty when no
            registry is wired, or when resolution degraded to an
            ephemeral key (nothing to list -- an ephemeral owner never
            holds a background job, since ``start_background`` refuses
            that case outright).
        """
        if self._background_jobs is None:
            return ()
        owner_key, strategy_owns = self._resolve_background_owner_key(
            owner_id, project_id=project_id, category=category
        )
        if not strategy_owns:
            return ()
        return await self._background_jobs.list_by_owner(owner_key)


__all__: list[str] = []
