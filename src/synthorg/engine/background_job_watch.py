"""Stall nudge for background shell commands.

A ``shell_command(background=True)`` call detaches a process the agent
may simply forget about across turns. ``BackgroundJobWatchChannel``
(``background_job_watch_channel.py``) records the job ids the loop
itself observed a tool call return (see ``loop_tool_execution.py``'s
``execute_tool_calls``, the only writer of *new* records); the
``BackgroundJobWatcher`` here reads that channel at the existing
turn-boundary slot beside ``check_steering`` and nudges the agent once a
watched job has been running quietly past a configurable threshold.

One implementation with an on/off switch, not a pluggable family like
``StagnationDetectionConfig``: there is exactly one way to watch a
background job, so a strategy discriminator would name a choice nobody
makes.
"""

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.background_job_watch_channel import WatchedJobRecord
from synthorg.engine.context import AgentContext
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_BACKGROUND_JOB_NUDGED,
    EXECUTION_BACKGROUND_JOB_WATCH_DROPPED,
    EXECUTION_BACKGROUND_JOB_WATCH_READ_FAILED,
)
from synthorg.persistence.background_job_protocol import (
    LIVE_BACKGROUND_JOB_STATUSES,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry

logger = get_logger(__name__)


class BackgroundJobStalenessConfig(BaseModel):
    """Selects whether the background-job stall nudge is active.

    Off by default: an agent that never backgrounds a shell command pays
    nothing, and an operator opts in per deployment.

    Attributes:
        enabled: Whether the stall nudge is active.
        nudge_after_seconds: How long a watched job must have run
            quietly (since it started, or since its last nudge) before
            the agent is nudged again.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether the background-job stall nudge is active",
    )
    nudge_after_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Seconds a watched job runs quietly before a nudge",
    )


def _nudge_message(
    record: WatchedJobRecord, elapsed_seconds: float, command_repr: str
) -> ChatMessage:
    """Build the USER message reminding the agent of a stalled job.

    *command_repr* is agent-authored shell text (the command the agent
    itself started), so it is fenced the same way ``loop_empty_run.py``
    fences a planner-authored deliverable path before it reaches a
    prompt boundary.

    Returns:
        A USER-role message naming the job and its elapsed run time.
    """
    fenced = wrap_untrusted(TAG_TASK_DATA, command_repr)
    return ChatMessage(
        role=MessageRole.USER,
        content=(
            f"Background job {record.job_id} ({fenced}) has been running for "
            f"about {int(elapsed_seconds)}s since you started it. Check on it "
            "with check_background_job / read_background_job_output, or "
            "cancel it with cancel_background_job if it is no longer needed."
        ),
    )


class BackgroundJobWatcher:
    """Watches the jobs an ``AgentContext`` knows it started, and nudges."""

    def __init__(
        self,
        registry: BackgroundJobRegistry,
        config: BackgroundJobStalenessConfig,
    ) -> None:
        """Initialise the watcher.

        Args:
            registry: Read surface over persisted background job rows.
            config: Staleness threshold configuration.
        """
        self._registry = registry
        self._config = config

    async def check(self, ctx: AgentContext, *, clock: Clock) -> AgentContext | None:
        """Nudge the agent about any watched job stalled past the threshold.

        Drops a watched record once its job has left a live status (or
        vanished), so a finished job is never nudged about again. The
        threshold is judged against time since the *last* nudge (or since
        watching began, if never nudged); the nudge message itself instead
        reports total time watched, since that -- not the interval between
        nudges -- is what the agent needs to decide whether to keep waiting.

        Returns:
            The updated context when a job was nudged or dropped, or
            ``None`` when nothing changed.
        """
        channel = ctx.background_job_watch
        if not channel.records:
            return None
        now = clock.now()
        changed = False
        messages: list[ChatMessage] = []
        jobs = await asyncio.gather(
            *(self._registry.get(record.job_id) for record in channel.records)
        )
        for record, job in zip(channel.records, jobs, strict=True):
            if job is None or job.status not in LIVE_BACKGROUND_JOB_STATUSES:
                channel = channel.without_record(record.job_id)
                changed = True
                logger.info(
                    EXECUTION_BACKGROUND_JOB_WATCH_DROPPED,
                    execution_id=ctx.execution_id,
                    job_id=record.job_id,
                    status=job.status if job is not None else None,
                )
                continue
            since = record.last_nudged_at or record.started_watching_at
            elapsed = (now - since).total_seconds()
            if elapsed < self._config.nudge_after_seconds:
                continue
            messages.append(
                _nudge_message(
                    record,
                    (now - record.started_watching_at).total_seconds(),
                    job.command_repr,
                )
            )
            channel = channel.with_record(
                record.model_copy(update={"last_nudged_at": now})
            )
            changed = True
            logger.info(
                EXECUTION_BACKGROUND_JOB_NUDGED,
                execution_id=ctx.execution_id,
                job_id=record.job_id,
                elapsed_seconds=int(elapsed),
            )
        if not changed:
            return None
        updated = ctx.with_background_job_watch(channel)
        for message in messages:
            updated = updated.with_message(message)
        return updated


def create_background_job_watcher(
    config: BackgroundJobStalenessConfig,
    *,
    registry: BackgroundJobRegistry | None,
) -> BackgroundJobWatcher | None:
    """Build the watcher selected by *config*.

    Args:
        config: Staleness threshold configuration.
        registry: Read surface over persisted background job rows, or
            ``None`` when the feature is unwired (no persistence
            connected -- in which case no background job could ever
            have started, so there is nothing to watch either way).

    Returns:
        A :class:`BackgroundJobWatcher`, or ``None`` when the nudge is
        disabled or no registry is available.
    """
    if not config.enabled or registry is None:
        return None
    return BackgroundJobWatcher(registry, config)


async def check_background_job_watch(
    ctx: AgentContext,
    watcher: BackgroundJobWatcher | None,
    *,
    clock: Clock,
) -> AgentContext | None:
    """Consult the stall-nudge watcher at a turn boundary.

    Mirrors ``check_steering``'s exact shape: best-effort, so a registry
    hiccup never interrupts an otherwise-healthy loop.

    Args:
        ctx: Current agent context.
        watcher: The stall-nudge watcher; ``None`` disables the nudge.
        clock: Clock seam for staleness comparisons.

    Returns:
        The updated context when a job was nudged or dropped; ``None``
        when there was nothing to do.
    """
    if watcher is None:
        return None
    try:
        return await watcher.check(ctx, clock=clock)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_BACKGROUND_JOB_WATCH_READ_FAILED,
            execution_id=ctx.execution_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = [
    "BackgroundJobStalenessConfig",
    "BackgroundJobWatcher",
    "check_background_job_watch",
    "create_background_job_watcher",
]
