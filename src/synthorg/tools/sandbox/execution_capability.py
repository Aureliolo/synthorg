# module-kind: code
"""Whether this process can execute an agent tool at all, asked once at boot.

An agent tool is a subprocess or a container, and a deployment can be unable to
start either while every other part of the product works. Run 3 of the live
dogfood was exactly that: planning and review ran, every shelling tool died at
invocation with ``NotImplementedError``, agents kept going to turn 16 and then
failed, and the run read as a model problem for two full attempts.

So the condition is asked here, before an agent can find it, and stated in terms
of what it costs rather than what it is. Two probes, because there are two ways
to lose the tool plane and they lose different tools:

* **Spawn.** ``asyncio.create_subprocess_exec`` is unimplemented on the Windows
  ``SelectorEventLoop``, which psycopg's async pool requires, so a native
  Windows backend on Postgres can drive the database or spawn a process, never
  both. Losing it takes out the git, file-system and subprocess-sandbox tools.
* **Container.** ``code_execution`` and ``terminal`` are pinned to the container
  backend by :mod:`synthorg.tools.sandbox.sandboxing_config` and cannot be
  overridden off it, so an unreachable daemon (or a workspace this process
  cannot describe to it) means no shell command runs and no
  ``CodeExecutionRecord`` is ever written, which is what makes the
  INTEGRATE/EVALUATE tail unreachable.

The probes report; they never raise. A caller that wants a failure out of them
(the subsystem activation) turns the reason into one, so the condition arrives
as something the operator surface can name rather than as a crash.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import aiodocker

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_EXECUTION_CAPABILITY_PROBED,
)
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.workspace_mount import (
    OwnContainer,
    WorkspaceMount,
    discover_own_container,
    resolve_workspace_mount,
)

logger = get_logger(__name__)

#: What the spawn probe runs. ``git`` is already asserted onto PATH by the boot
#: binary preflight, so a failure here cannot be "the image lost the binary"
#: unless the preflight is also failing, and ``--version`` touches nothing.
_PROBE_BINARY: Final[str] = "git"
_PROBE_ARGS: Final[tuple[str, ...]] = ("--version",)

#: Both probes are bounded so a wedged daemon or a hung child cannot hold boot
#: open. Generous against the milliseconds each costs in practice: the point is
#: a ceiling, not a deadline.
_PROBE_TIMEOUT_SECONDS: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """What one probe found.

    Attributes:
        available: Whether the thing probed can be used.
        reason: Why not, in terms of the tools it costs. Required whenever
            *available* is false, because an unavailable outcome with nothing
            to say is the "declined on a condition it does not declare" the
            subsystem surface exists to prevent.
        error: The exception behind *reason*, when one was raised. Kept for
            callers that need the type; never rendered into a message here.
    """

    available: bool
    reason: str | None = None
    error: BaseException | None = None

    def __post_init__(self) -> None:
        """Refuse an unavailable outcome that names no condition.

        Raises:
            ValueError: *available* is false with no *reason*.
        """
        if not self.available and not self.reason:
            msg = "an unavailable ProbeOutcome must carry a reason"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ToolExecutionCapability:
    """What this process can do when an agent calls a tool.

    Attributes:
        subprocess: Whether a child process can be started at all.
        container: Whether the container backend can be reached and given the
            workspace.
        workspace_mount: How a sandbox reaches the workspace, when this process
            is containerised and the container probe resolved it.
    """

    subprocess: ProbeOutcome
    container: ProbeOutcome
    workspace_mount: WorkspaceMount | None = None

    @property
    def can_execute(self) -> bool:
        """Whether every tool the product ships can actually run.

        Returns:
            Whether both probes found their half available.
        """
        return self.subprocess.available and self.container.available

    @property
    def decline_reason(self) -> str | None:
        """Why the tool plane is not up, or ``None`` when it is.

        Both halves are reported when both are missing: fixing one and finding
        the other still broken is the loop this exists to cut short.

        Returns:
            The joined reasons, or ``None``.
        """
        reasons = [
            outcome.reason
            for outcome in (self.subprocess, self.container)
            if outcome.reason is not None
        ]
        return "; ".join(reasons) if reasons else None


async def probe_subprocess_spawn() -> ProbeOutcome:
    """Find out whether this process can start a child at all.

    Runs the real call rather than inspecting the event-loop class: the
    question is "can this process spawn", and only actually spawning also
    catches a seccomp profile, a stripped image or an exhausted process table.

    Returns:
        The outcome, naming the tools a failure costs.
    """
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            process = await asyncio.create_subprocess_exec(
                _PROBE_BINARY,
                *_PROBE_ARGS,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
    except NotImplementedError as exc:
        reason = (
            "this process's event loop cannot spawn a subprocess, so every "
            "git, file-system and subprocess-sandboxed tool fails the moment "
            "an agent calls it. On Windows only the ProactorEventLoop "
            "implements subprocesses, and psycopg's async pool requires the "
            "SelectorEventLoop, so a native Windows backend on Postgres can "
            "have one or the other; run the backend in a container instead"
        )
        return ProbeOutcome(available=False, reason=reason, error=exc)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        reason = (
            f"the {_PROBE_BINARY!r} probe could not run "
            f"({safe_error_description(exc)}), so whether this process can "
            "spawn a child is unknown and the tools that need one are unsafe "
            "to offer"
        )
        return ProbeOutcome(available=False, reason=reason, error=exc)
    return ProbeOutcome(available=True)


async def probe_container_backend(
    *,
    workspace: Path,
    docker: aiodocker.Docker | None = None,
    own: OwnContainer | None = None,
) -> tuple[ProbeOutcome, WorkspaceMount | None]:
    """Find out whether the container backend can run a tool for this process.

    Reaching the daemon is not enough to answer it. A daemon that would hand
    every sandbox an empty ``/workspace`` (see
    :mod:`synthorg.tools.sandbox.workspace_mount`) runs commands that find
    nothing and report ordinary failures, so the workspace is resolved here
    too and a failure to resolve counts as the backend being unavailable.

    Args:
        workspace: The agent workspace root this deployment writes through.
        docker: A client to use; one is opened and closed when omitted.
        own: Which container this process is; discovered when omitted.

    Returns:
        The outcome and, when this process is containerised, the mount a
        sandbox would be given.
    """
    identity = own if own is not None else discover_own_container()
    owns_client = docker is None
    client: aiodocker.Docker | None = None
    try:
        # Constructed INSIDE the guard: with no socket to find, aiodocker
        # asserts rather than returning, and an AssertionError escaping here
        # makes the subsystem read `failed` (an activation that crashed)
        # instead of `blocked` with a reason, which is the whole difference
        # this probe exists to make.
        client = docker if docker is not None else aiodocker.Docker()
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            version = await client.version()
            api_version = version.get("ApiVersion")
            mount = await resolve_workspace_mount(
                docker=client,
                root=workspace,
                api_version=api_version if isinstance(api_version, str) else "",
                container_id=identity.container_id,
                certain=identity.certain,
            )
    except SandboxError as exc:
        reason = (
            f"the container backend cannot be given the workspace: {exc}. "
            "The terminal and code_execution tools are pinned to that backend, "
            "so nothing an agent runs would see the project's files"
        )
        return ProbeOutcome(available=False, reason=reason, error=exc), None
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        reason = (
            f"the Docker daemon is unreachable ({safe_error_description(exc)}), "
            "so the terminal and code_execution tools, which are pinned to the "
            "container backend and cannot be moved off it, cannot run and no "
            "CodeExecutionRecord can be written for the build/test oracle"
        )
        return ProbeOutcome(available=False, reason=reason, error=exc), None
    finally:
        if owns_client and client is not None:
            await client.close()
    return ProbeOutcome(available=True), mount


async def probe_tool_execution(
    *,
    workspace: Path,
    docker: aiodocker.Docker | None = None,
    own: OwnContainer | None = None,
) -> ToolExecutionCapability:
    """Ask both halves of the tool plane whether they are there.

    Args:
        workspace: The agent workspace root this deployment writes through.
        docker: A client to use; one is opened and closed when omitted.
        own: Which container this process is; discovered when omitted.

    Returns:
        The report, which names every condition it found.
    """
    subprocess_outcome = await probe_subprocess_spawn()
    container_outcome, mount = await probe_container_backend(
        workspace=workspace,
        docker=docker,
        own=own,
    )
    capability = ToolExecutionCapability(
        subprocess=subprocess_outcome,
        container=container_outcome,
        workspace_mount=mount,
    )
    logger.info(
        SANDBOX_EXECUTION_CAPABILITY_PROBED,
        can_spawn_subprocess=subprocess_outcome.available,
        container_backend_available=container_outcome.available,
        workspace=str(workspace),
        volume=None if mount is None else mount.volume,
        subpath=None if mount is None else mount.subpath,
    )
    return capability


__all__ = [
    "ProbeOutcome",
    "ToolExecutionCapability",
    "probe_container_backend",
    "probe_subprocess_spawn",
    "probe_tool_execution",
]
