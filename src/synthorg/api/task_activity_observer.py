# module-kind: adapter
"""Task-lifecycle activity observer.

Registered on the :class:`TaskEngine` as a best-effort observer. For every
transition that carries a task and a status it publishes a run-outcome-aware
``task.status_changed`` event to the ``tasks`` WebSocket channel so the
dashboard Live Activity feed reflects actual task execution; and, once per run
(when a task first enters a terminal run state from a non-terminal one), it
records a :class:`TaskMetricRecord` so org health, the completions sparkline,
and the REST activity timeline derive from real outcomes rather than a signal
no production code ever emitted.

Both side effects are independently guarded: a publish fault never blocks the
metric record and vice versa, and neither ever propagates out of the observer
(the ``TaskEngine`` dispatch loop treats observers as best-effort anyway).
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.api.channels import CHANNEL_TASKS
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.api.ws_payloads._lifecycle import WsTaskStatusChangedPayload
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.artifact import Artifact
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.run_outcome import (
    TERMINAL_RUN_STATES,
    RunOutcome,
    derive_run_outcome,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.task import (
    TASK_ACTIVITY_METRIC_RECORD_FAILED,
    TASK_ACTIVITY_OUTCOME_RESOLVE_FAILED,
    TASK_ACTIVITY_PUBLISH_FAILED,
)

logger = get_logger(__name__)

# Upper bound on the task title echoed into the WS ``description``. A title is
# free-form and unbounded at the model; a status-changed event fans out to every
# subscriber on every transition, so the broadcast copy is truncated.
_MAX_DESCRIPTION_TITLE_LENGTH: Final[int] = 120


class ActivityAgentRef(BaseModel):
    """Resolved display identity of a task's assignee for the activity feed.

    Attributes:
        name: Agent display name.
        role: Agent role (its "function").
        department: Agent department.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr


type ArtifactLister = Callable[[str], Awaitable[Sequence[Artifact]]]
type MetricRecorder = Callable[[TaskMetricRecord], Awaitable[object]]
#: Resolve an agent id to its display identity, or ``None`` when unknown.
#: Injected so the observer can name the assignee without a registry import;
#: best-effort, so a fault or unknown id simply yields an unnamed row.
type AgentRefResolver = Callable[[str], Awaitable[ActivityAgentRef | None]]
#: Return whether the build/test oracle blocks a task's completion, so the feed
#: shows a truthful FAILED outcome for a code task whose tests failed / never
#: ran. Injected + best-effort: a fault yields ``False`` (no block shown).
type OracleBlockResolver = Callable[[Task], Awaitable[bool]]
#: Publish a serialised ``WsEvent`` to the named channels. Matches the
#: positional ``ChannelsPlugin.wait_published(data, channels)`` surface: the
#: boot wiring passes that bound method (NOT ``publish``) so delivery goes
#: straight to the backend rather than the plugin's background pub queue. That
#: queue's worker races its own teardown (``task_done`` on a ``None`` queue)
#: when a transition publishes during lifespan shutdown, since Litestar unwinds
#: the channels plugin before the on_shutdown hook that stops the task engine;
#: the direct path sidesteps that worker entirely.
type PublishFn = Callable[[str, list[str]], Awaitable[None]]


class TaskActivityObserver:
    """Publishes task transitions to WS and records terminal-run metrics.

    Injected collaborators keep it unit-testable without a live app: a publish
    callable for WS delivery, an artifact lister for the empty/succeeded split,
    and a metric recorder for the once-per-run outcome.
    """

    def __init__(
        self,
        *,
        publish: PublishFn,
        list_artifacts: ArtifactLister,
        record_metric: MetricRecorder,
        resolve_agent: AgentRefResolver | None = None,
        oracle_block_for: OracleBlockResolver | None = None,
    ) -> None:
        self._publish_fn = publish
        self._list_artifacts = list_artifacts
        self._record_metric = record_metric
        self._resolve_agent = resolve_agent
        self._oracle_block_for = oracle_block_for

    async def __call__(self, event: TaskStateChanged) -> None:
        """Handle one task state change (WS publish + terminal metric record)."""
        task = event.task
        new_status = event.new_status
        if task is None or new_status is None:
            # Deletes and non-status mutations carry no run-outcome signal.
            return
        outcome = await self._resolve_outcome(task, new_status)
        await self._publish(event, task, new_status, outcome)
        if new_status in TERMINAL_RUN_STATES and (
            event.previous_status not in TERMINAL_RUN_STATES
        ):
            await self._record(task, outcome, event)

    async def _resolve_outcome(
        self, task: Task, new_status: TaskStatus
    ) -> RunOutcome | None:
        """Resolve the truthful run outcome for a terminal transition.

        ``None`` for a non-terminal transition (no outcome yet) or when the
        artifact listing is unavailable for a non-failed terminal run (unknown,
        never a fabricated EMPTY). ``_record`` treats that ``None`` as
        unclassifiable and records no metric, so an unknown outcome never
        becomes a fabricated success.

        Returns:
            The run outcome, or ``None`` when none can be shown truthfully.
        """
        if new_status not in TERMINAL_RUN_STATES:
            return None
        if new_status == TaskStatus.FAILED:
            return RunOutcome.FAILED
        try:
            produced = await self._list_artifacts(str(task.id))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort artifact count for the feed
            reraise_critical(exc)
            logger.warning(
                TASK_ACTIVITY_OUTCOME_RESOLVE_FAILED,
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        oracle_blocked = await self._resolve_oracle_block(task)
        return derive_run_outcome(
            status=new_status,
            produced_artifact_count=len(produced),
            oracle_blocked=oracle_blocked,
        )

    async def _resolve_oracle_block(self, task: Task) -> bool:
        """Best-effort build/test-oracle block check for the outcome.

        Mirrors the approvals read surface so the live feed and the approvals
        queue agree on an oracle-blocked run. A missing resolver or a fault
        yields ``False`` (no block shown), never a fabricated success.

        Returns:
            ``True`` when the oracle blocks the task's completion.
        """
        if self._oracle_block_for is None:
            return False
        try:
            return await self._oracle_block_for(task)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort feed enrichment; a block
            # check fault must not suppress the outcome (the authoritative gate
            # already ran on the completion path).
            reraise_critical(exc)
            logger.warning(
                TASK_ACTIVITY_OUTCOME_RESOLVE_FAILED,
                task_id=str(task.id),
                stage="oracle",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False

    async def _publish(
        self,
        event: TaskStateChanged,
        task: Task,
        new_status: TaskStatus,
        outcome: RunOutcome | None,
    ) -> None:
        """Publish the ``task.status_changed`` event to the tasks WS channel."""
        agent_ref = await self._resolve_agent_ref(task.assigned_to)
        payload = WsTaskStatusChangedPayload(
            task_id=str(task.id),
            from_status=(
                event.previous_status.value if event.previous_status else None
            ),
            to_status=new_status.value,
            assigned_to=task.assigned_to,
            agent_name=agent_ref.name if agent_ref else None,
            agent_role=agent_ref.role if agent_ref else None,
            department=agent_ref.department if agent_ref else None,
            description=_describe(task, new_status, outcome),
            run_outcome=outcome,
        )
        ws_event = WsEvent(
            event_type=WsEventType.TASK_STATUS_CHANGED,
            channel=CHANNEL_TASKS,
            timestamp=event.timestamp,
            payload=payload.model_dump(mode="json"),
        )
        try:
            await self._publish_fn(ws_event.model_dump_json(), [CHANNEL_TASKS])
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort live-activity fan-out
            reraise_critical(exc)
            logger.warning(
                TASK_ACTIVITY_PUBLISH_FAILED,
                task_id=str(task.id),
                stage="publish",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _resolve_agent_ref(
        self, assigned_to: str | None
    ) -> ActivityAgentRef | None:
        """Resolve the assignee's display identity, best-effort.

        Returns:
            The resolved :class:`ActivityAgentRef`, or ``None`` when there is
            no assignee, no resolver wired, or resolution faults (the feed row
            then simply shows no agent name rather than blocking the publish).
        """
        if assigned_to is None or self._resolve_agent is None:
            return None
        try:
            return await self._resolve_agent(assigned_to)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort name enrichment for the feed
            reraise_critical(exc)
            logger.warning(
                TASK_ACTIVITY_PUBLISH_FAILED,
                agent_id=assigned_to,
                stage="agent_resolve",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _record(
        self,
        task: Task,
        outcome: RunOutcome | None,
        event: TaskStateChanged,
    ) -> None:
        """Record the terminal run as a TaskMetricRecord (once per run).

        A run is a success only when it genuinely produced output: a failed or
        empty run records ``is_success=False`` so the org-health success rate
        cannot be inflated by empty runs. An unresolved outcome (``None`` from
        an artifact-count fault) records nothing rather than guessing a
        success. Execution telemetry (duration / cost / tokens) is left unset
        (``None``): a state transition carries a truthful reliability outcome
        but no measured cost or latency, so the efficiency pillar reads it as
        unmeasured rather than a fabricated zero.
        """
        if task.assigned_to is None:
            # No assignee to attribute the outcome to; the WS event still fired.
            return
        if outcome is None:
            # Outcome could not be resolved (artifact-count fault); record
            # nothing rather than credit an unknown run as a success.
            return
        is_success = outcome not in (RunOutcome.FAILED, RunOutcome.EMPTY)
        try:
            record = TaskMetricRecord(
                agent_id=task.assigned_to,
                task_id=str(task.id),
                task_type=task.type,
                completed_at=event.timestamp,
                is_success=is_success,
                # Persist the classified outcome so the historical (REST)
                # activity feed can distinguish an empty run from a hard
                # failure -- ``is_success`` alone collapses both to non-success.
                run_outcome=outcome,
                currency=DEFAULT_CURRENCY,
                complexity=task.estimated_complexity,
            )
            await self._record_metric(record)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort metric capture for health
            reraise_critical(exc)
            logger.warning(
                TASK_ACTIVITY_METRIC_RECORD_FAILED,
                task_id=str(task.id),
                agent_id=task.assigned_to,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def _describe(task: Task, new_status: TaskStatus, outcome: RunOutcome | None) -> str:
    """Build an outcome-aware human description for the activity feed row.

    Returns:
        A short present-tense description of the transition.
    """
    title = _truncate_title(task.title)
    if outcome == RunOutcome.FAILED:
        return f"{title} failed"
    if outcome == RunOutcome.EMPTY:
        return f"{title} finished but produced nothing"
    if outcome == RunOutcome.SUCCEEDED:
        return f"{title} completed"
    return f"{title}: {new_status.value.replace('_', ' ')}"


def _truncate_title(title: str) -> str:
    """Bound an unbounded task title before it is broadcast to every client.

    Returns:
        The title, truncated with an ellipsis when it exceeds the cap.
    """
    if len(title) <= _MAX_DESCRIPTION_TITLE_LENGTH:
        return title
    return title[: _MAX_DESCRIPTION_TITLE_LENGTH - 3] + "..."
