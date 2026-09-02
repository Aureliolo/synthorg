# module-kind: code
"""Release a reusable sandbox container once the run that owns it has finished.

Boot reconciliation (:mod:`synthorg.tools.sandbox.reconciliation`) answers
"is there a row for this container", once, at a moment this process can have
created none of its own, and that grounding is right for that moment and
wrong for every later one: mid-process, a container with no row may be one
somebody is creating right now. So this is a SECOND pass on a different
question. It walks the keys the lifecycle strategy itself holds and asks of
each owner whether its run has finished, which the task table answers: a
per-task owner whose task is no longer assigned, in progress or in review,
or a per-agent owner with no such task at all. A container whose owner has
finished is released through the same path the execution service releases
it on, so the lifecycle's own grace window, idle timer and background-job
pin all still apply; this sweep never destroys anything itself.

Level-triggered on the same shape as run recovery: boot is the first pass
and every tick is the same idempotent question. A key the sweep cannot read
is reported and left alone, because a container it cannot attribute is one
it cannot know to be finished.

The decision and the release are two steps with a task-table read between
them, and a run can reacquire the key in that gap: a warm reacquire hands
back the same container, so nothing about the container says it is back in
use. Every key is therefore read WITH the generation the lifecycle holds it
under and released against that generation, and the lifecycle refuses a
release whose generation has moved on.
"""

import re
from dataclasses import dataclass
from typing import Final, Protocol, override, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_RECLAIM_GROUNDING_FAILED,
    SANDBOX_RECLAIM_OWNER_RELEASED,
    SANDBOX_RECLAIM_OWNER_UNPARSEABLE,
    SANDBOX_RECLAIM_PAUSED,
    SANDBOX_RECLAIM_RELEASE_FAILED,
    SANDBOX_RECLAIM_SCHEDULER_FAILED,
    SANDBOX_RECLAIM_SCHEDULER_STARTED,
    SANDBOX_RECLAIM_SCHEDULER_STOPPED,
    SANDBOX_RECLAIM_SWEEP,
)
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.sandbox._mount_mode import MOUNT_MODES
from synthorg.tools.sandbox.lifecycle.config import LifecycleStrategy
from synthorg.tools.sandbox.lifecycle.protocol import TrackedOwner

logger = get_logger(__name__)


@runtime_checkable
class ReclaimableSandbox(Protocol):
    """A backend that holds containers past a call and can name their owners.

    Narrower than ``SandboxBackend`` on purpose: only a backend with a
    reusing lifecycle has anything to reclaim, and a backend that does not
    (the subprocess one) is declined by name rather than asked to answer
    empty. The Docker backend satisfies it structurally.
    """

    async def tracked_owners(self) -> tuple[TrackedOwner, ...]:
        """The fully qualified lifecycle keys holding a container right now.

        Returns:
            The keys exactly as the lifecycle holds them, each with the
            generation it is held under.
        """
        ...

    async def release_key(self, owner_key: str, *, generation: int) -> None:
        """Release one lifecycle key exactly as it is held.

        The complement of ``release_owner``, which rebuilds the keys from an
        owner and its project. The sweep already holds the qualified key and
        cannot rebuild it (the image segment is a digest of an image it no
        longer knows), so it releases by key.

        Args:
            owner_key: A key :meth:`tracked_owners` returned.
            generation: The generation it was returned under. A key acquired
                again since is refused rather than released.
        """
        ...


#: Cadence when the operator has set none. Five minutes: a forgotten release
#: costs a warm container's memory and its pinned image for as long as the
#: sweep takes to notice, and a run's own release fires at the task boundary
#: so the sweep is the backstop rather than the mechanism.
DEFAULT_RECLAIM_INTERVAL_SECONDS: Final[float] = 300.0

#: The statuses under which a task still has a run that may need its
#: container. Everything else is finished, parked with nothing executing, or
#: waiting on a person, and a container held across a human's wait is the
#: leak this sweep exists to end.
RUNNING_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW}
)

#: The environment-image segment ``project_prefixed`` appends: a fixed-width
#: truncated digest, so it is recognised by shape rather than by knowing the
#: image it was taken over.
_IMAGE_SEGMENT: Final[re.Pattern[str]] = re.compile(r":img-[0-9a-f]{12}$")

_BOOT_TRIGGER: Final[str] = "boot"
_PERIODIC_TRIGGER: Final[str] = "periodic"


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerKey:
    """A qualified lifecycle key, read back into the parts it was built from.

    Keyword-only because four of its fields are strings of the same shape,
    and a positional construction that transposed two of them would type
    check and release somebody else's container.

    Attributes:
        key: The key exactly as the lifecycle holds it.
        project_id: The project prefix, or ``None`` when the key carried
            none.
        owner: The agent id (per-agent) or task id (per-task) the container
            was acquired for; never blank, since a key with no owner segment
            is not an owner key at all.
        image_segment: The environment-image digest segment, if any.
        mount_mode: The workspace mount mode suffix, if any.
    """

    key: str
    project_id: str | None
    owner: NotBlankStr
    image_segment: str | None
    mount_mode: str | None


def parse_owner_key(key: str) -> OwnerKey | None:
    """Read a qualified lifecycle key back into its parts.

    The inverse of ``_owner_key.project_prefixed``. The owner is the LAST
    colon-separated segment once the mount-mode and image suffixes are
    stripped, which is unambiguous because ``_owner_key.valid_raw_owner``
    refuses a colon in the unqualified owner id, while a project id may carry
    anything ``valid_owner`` admits.

    Returns:
        The parts, or ``None`` when the key has no owner segment.
    """
    rest = key
    mount_mode: str | None = None
    for mode in MOUNT_MODES:
        suffix = f":{mode}"
        if rest.endswith(suffix):
            mount_mode = str(mode)
            rest = rest[: -len(suffix)]
            break
    image_segment: str | None = None
    match = _IMAGE_SEGMENT.search(rest)
    if match is not None:
        image_segment = match.group(0)[1:]
        rest = rest[: match.start()]
    project, _, owner = rest.rpartition(":")
    if not owner:
        return None
    return OwnerKey(
        key=key,
        project_id=project or None,
        owner=NotBlankStr(owner),
        image_segment=image_segment,
        mount_mode=mount_mode,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReclaimOutcome:
    """What one sweep did.

    Three tuples of the same type, so keyword-only: nothing but the name
    tells a released key from a kept one.

    Attributes:
        released: Keys whose owner had finished and were handed to the
            lifecycle's release.
        kept: Keys whose owner still has a run.
        unparseable: Keys the sweep could not attribute and left alone.
    """

    released: tuple[str, ...]
    kept: tuple[str, ...]
    unparseable: tuple[str, ...]


class SandboxOwnerReclaimer:
    """Asks, of every held container, whether its owner's run has finished.

    Args:
        backend: The reusable sandbox backend whose lifecycle holds the
            containers.
        strategy_kind: Which lifecycle the backend reuses under, which
            decides what an owner segment names.
        tasks: Where an owner's run state is read from.
    """

    def __init__(
        self,
        *,
        backend: ReclaimableSandbox,
        strategy_kind: LifecycleStrategy,
        tasks: TaskRepository,
    ) -> None:
        self._backend = backend
        self._strategy_kind = strategy_kind
        self._tasks = tasks

    async def reconcile(self, *, trigger: str) -> ReclaimOutcome:
        """Run one pass over every held key.

        Args:
            trigger: What started the pass, for the log.

        Returns:
            What the pass did.
        """
        released: list[str] = []
        kept: list[str] = []
        unparseable: list[str] = []
        for tracked in await self._backend.tracked_owners():
            key = str(tracked.key)
            parsed = parse_owner_key(key)
            if parsed is None:
                logger.warning(SANDBOX_RECLAIM_OWNER_UNPARSEABLE, owner_id=key)
                unparseable.append(key)
                continue
            if not await self._owner_finished(parsed):
                kept.append(key)
                continue
            if await self._release(parsed, generation=tracked.generation):
                released.append(key)
            else:
                kept.append(key)
        logger.info(
            SANDBOX_RECLAIM_SWEEP,
            trigger=trigger,
            strategy=self._strategy_kind,
            released=len(released),
            kept=len(kept),
            unparseable=len(unparseable),
        )
        return ReclaimOutcome(
            released=tuple(released),
            kept=tuple(kept),
            unparseable=tuple(unparseable),
        )

    async def _owner_finished(self, parsed: OwnerKey) -> bool:
        """Whether *parsed*'s owner has no run that may still need a container.

        A read that fails answers ``False``: a container whose owner cannot
        be read is one this pass cannot know to be finished, and the next
        pass asks again.

        Returns:
            ``True`` when the owner's run is over.
        """
        try:
            match self._strategy_kind:
                case LifecycleStrategy.PER_TASK:
                    task = await self._tasks.get(parsed.owner)
                    return task is None or task.status not in RUNNING_STATUSES
                case LifecycleStrategy.PER_AGENT:
                    return not await self._agent_has_a_run(parsed.owner)
                case LifecycleStrategy.PER_CALL:
                    # Holds nothing past a command, so there is nothing to
                    # attribute and nothing this sweep may release.
                    return False
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SANDBOX_RECLAIM_GROUNDING_FAILED,
                owner_id=parsed.key,
                strategy=self._strategy_kind,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False

    async def _agent_has_a_run(self, agent_id: str) -> bool:
        """Whether any task assigned to *agent_id* is still running.

        Returns:
            ``True`` when one is.
        """
        for status in RUNNING_STATUSES:
            rows = await self._tasks.query(
                TaskFilterSpec(status=status, assigned_to=NotBlankStr(agent_id)),
                limit=1,
            )
            if rows:
                return True
        return False

    async def _release(self, parsed: OwnerKey, *, generation: int) -> bool:
        """Hand one finished owner's key to the lifecycle's own release.

        A failed release is logged and the key is kept, so one container's
        fault never stops the sweep reaching the rest; the next pass
        retries it. The generation travels with the key so a run that
        reacquired it while the task table was being read keeps its
        container.

        Returns:
            Whether the release was accepted.
        """
        try:
            await self._backend.release_key(parsed.key, generation=generation)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SANDBOX_RECLAIM_RELEASE_FAILED,
                owner_id=parsed.key,
                strategy=self._strategy_kind,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        logger.info(
            SANDBOX_RECLAIM_OWNER_RELEASED,
            owner_id=parsed.key,
            owner=parsed.owner,
            project_id=parsed.project_id,
            strategy=self._strategy_kind,
        )
        return True


class SandboxReclaimScheduler(AsyncCycleScheduler):
    """Runs the reclamation sweep on a cadence.

    Args:
        reclaimer: The sweep to run.
        interval_seconds: Starting cadence; re-resolved per tick so an
            operator change applies without a restart.
        config_resolver: Reads the live cadence and the pause switch.
            ``None`` keeps the construction-time cadence for the process's
            life and leaves the sweep unpausable.
    """

    def __init__(
        self,
        reclaimer: SandboxOwnerReclaimer,
        *,
        interval_seconds: float = DEFAULT_RECLAIM_INTERVAL_SECONDS,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="sandbox-reclaim-sweep",
            started_event=SANDBOX_RECLAIM_SCHEDULER_STARTED,
            stopped_event=SANDBOX_RECLAIM_SCHEDULER_STOPPED,
            failed_event=SANDBOX_RECLAIM_SCHEDULER_FAILED,
        )
        self._reclaimer = reclaimer
        self._config_resolver = config_resolver

    @override
    async def _run_cycle_once(self) -> None:
        """Run one sweep."""
        await self._reclaimer.reconcile(trigger=_PERIODIC_TRIGGER)

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the sweep runs this tick.

        Fail-safe to running: a settings-backend outage must not leave
        every finished run's container held for the life of the process.

        Returns:
            ``True`` unless an operator has paused the sweep.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="tools",
            key="sandbox_reclaim_paused",
            fallback=False,
        )
        return not paused

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the sandbox vocabulary."""
        logger.debug(SANDBOX_RECLAIM_PAUSED, trigger=_PERIODIC_TRIGGER)

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence so a change applies without a restart.

        Namespace and key spelled out rather than read from class vars: the
        liveness gate reads the call site textually.

        Returns:
            The resolved cadence, or the construction value when no resolver
            is wired.
        """
        if self._config_resolver is None:
            return self._interval
        return await self._config_resolver.get_float(
            "tools", "sandbox_reclaim_interval_seconds"
        )


def boot_trigger() -> str:
    """The trigger label the wiring's first pass runs under.

    Returns:
        The label.
    """
    return _BOOT_TRIGGER


def describe_outcome(outcome: ReclaimOutcome) -> dict[str, int]:
    """The outcome as the counts a boot log line carries.

    Args:
        outcome: What a pass did.

    Returns:
        The counts.
    """
    return {
        "released": len(outcome.released),
        "kept": len(outcome.kept),
        "unparseable": len(outcome.unparseable),
    }


__all__ = [
    "DEFAULT_RECLAIM_INTERVAL_SECONDS",
    "RUNNING_STATUSES",
    "OwnerKey",
    "ReclaimOutcome",
    "ReclaimableSandbox",
    "SandboxOwnerReclaimer",
    "SandboxReclaimScheduler",
    "boot_trigger",
    "describe_outcome",
    "parse_owner_key",
]
