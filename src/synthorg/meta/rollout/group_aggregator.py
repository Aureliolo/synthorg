"""Per-group metric aggregation for rollout strategies.

A/B and canary rollouts need quality / success / spend samples
bucketed by which group an agent belongs to. The ``GroupSignalAggregator``
protocol captures that shape independently of the underlying data
source. The default implementation wraps ``PerformanceTracker`` and
extracts one sample per agent from its latest snapshot.
"""

import asyncio
from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ABTEST_GROUP_AGGREGATOR_AGENT_SKIPPED,
    META_ABTEST_GROUP_AGGREGATOR_SNAPSHOT_FAILED,
)

logger = get_logger(__name__)


class GroupSamples(BaseModel):
    """Per-observation samples for a group of agents.

    Sample tuples are aligned: ``agent_ids[i]`` is the source of
    ``quality_samples[i]``, ``success_samples[i]``, and
    ``spend_samples[i]``. Agents with incomplete data are excluded
    entirely so the tuples stay aligned.

    Attributes:
        agent_ids: Agents contributing samples.
        quality_samples: Quality scores (0-10).
        success_samples: Task success rates (0-1).
        spend_samples: Total spend per agent (display currency).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_ids: tuple[NotBlankStr, ...] = ()
    quality_samples: tuple[float, ...] = ()
    success_samples: tuple[float, ...] = ()
    spend_samples: tuple[float, ...] = ()

    @model_validator(mode="after")
    def _validate_aligned_tuples(self) -> Self:
        """Validate aligned tuples.

        Returns:
            The validated model instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        n = len(self.agent_ids)
        if not (
            len(self.quality_samples) == n
            and len(self.success_samples) == n
            and len(self.spend_samples) == n
        ):
            msg = (
                f"sample tuples must align with agent_ids (n={n}); got "
                f"quality={len(self.quality_samples)}, "
                f"success={len(self.success_samples)}, "
                f"spend={len(self.spend_samples)}"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class GroupSignalAggregator(Protocol):
    """Collects per-agent metric samples for a single rollout group."""

    async def aggregate_for_agents(
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
        since: datetime,
        until: datetime,
    ) -> GroupSamples:
        """Collect samples for the given agents over the time window.

        Args:
            agent_ids: Agents in the group.
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).

        Returns:
            Aligned sample tuples for quality / success / spend.
        """
        ...


class TrackerGroupAggregator:
    """Default ``GroupSignalAggregator`` backed by ``PerformanceTracker``.

    For each agent, reads the latest snapshot and extracts:

    * ``overall_quality_score`` → ``quality_samples``
    * ``windows[0].success_rate`` → ``success_samples``
    * ``windows[0].avg_cost_per_task * windows[0].tasks_completed`` →
      ``spend_samples``

    An agent contributes a triple only if every component is present.
    Missing components cause the whole triple to be skipped so the
    returned tuples stay aligned.
    """

    def __init__(self, *, tracker: PerformanceTracker) -> None:
        self._tracker = tracker

    async def aggregate_for_agents(
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
        since: datetime,
        until: datetime,
    ) -> GroupSamples:
        """Pull samples for each agent from the tracker in parallel.

        Per-agent snapshot fetches are isolated: a failure on one agent
        is logged and that agent is dropped from the returned tuples,
        but siblings continue. Only ``MemoryError`` and ``RecursionError``
        are re-raised so catastrophic states still propagate.

        Returns:
            ``GroupSamples`` instance.
        """
        # Tracker returns a point-in-time snapshot keyed by `now`; the
        # observation window bounds are enforced by the caller, so we
        # only need the upper bound here.
        _ = since
        if not agent_ids:
            return GroupSamples()
        async with asyncio.TaskGroup() as tg:
            snapshot_tasks = [
                tg.create_task(self._fetch_snapshot(agent_id, until))
                for agent_id in agent_ids
            ]
        kept_ids: list[NotBlankStr] = []
        qualities: list[float] = []
        successes: list[float] = []
        spends: list[float] = []
        for agent_id, task in zip(agent_ids, snapshot_tasks, strict=True):
            snapshot = task.result()
            if snapshot is None:
                continue
            triple = _extract_triple(snapshot)
            if triple is None:
                logger.debug(
                    META_ABTEST_GROUP_AGGREGATOR_AGENT_SKIPPED,
                    agent_id=str(agent_id),
                    reason="missing_metrics",
                )
                continue
            q, s, spend = triple
            kept_ids.append(agent_id)
            qualities.append(q)
            successes.append(s)
            spends.append(spend)
        return GroupSamples(
            agent_ids=tuple(kept_ids),
            quality_samples=tuple(qualities),
            success_samples=tuple(successes),
            spend_samples=tuple(spends),
        )

    async def _fetch_snapshot(
        self,
        agent_id: NotBlankStr,
        until: datetime,
    ) -> AgentPerformanceSnapshot | None:
        """Fetch one agent snapshot, isolating failures from siblings.

        Returns ``None`` when the tracker raises a recoverable
        ``Exception``; re-raises ``MemoryError`` and ``RecursionError``
        so catastrophic system errors are never swallowed.

        Returns:
            The ``AgentPerformanceSnapshot`` value when present, ``None`` otherwise.
        """
        try:
            return await self._tracker.get_snapshot(str(agent_id), now=until)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                META_ABTEST_GROUP_AGGREGATOR_SNAPSHOT_FAILED,
                agent_id=str(agent_id),
                error=type(exc).__name__,
            )
            return None


def _extract_triple(
    snapshot: AgentPerformanceSnapshot,
) -> tuple[float, float, float] | None:
    """Return ``(quality, success, spend)`` or ``None`` if incomplete.

    Returns:
        The ``tuple[float, float, float]`` value when present, ``None`` otherwise.
    """
    quality = snapshot.overall_quality_score
    if quality is None:
        return None
    if not snapshot.windows:
        return None
    window = snapshot.windows[0]
    success = window.success_rate
    cost_per_task = window.avg_cost_per_task
    if success is None or cost_per_task is None:
        return None
    spend = cost_per_task * float(window.tasks_completed)
    return quality, success, spend
