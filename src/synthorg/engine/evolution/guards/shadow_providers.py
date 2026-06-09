"""Shadow task provider strategies.

Two strategies ship out of the box:

* ``ConfiguredShadowTaskProvider`` -- reads a curated task suite from
  ``ShadowEvaluationConfig.probe_tasks``.  This is the safe default:
  operators curate representative tasks per agent / role and the guard
  always evaluates against the same canonical suite.
* ``RecentTaskHistoryProvider`` -- samples up to N recent completed
  tasks for the agent via a pluggable ``TaskSampler`` callable.  This
  is opt-in: it trades curation effort for drift (the suite reflects
  whatever the agent has been doing lately).

Both providers return up to ``sample_size`` tasks.  An empty tuple is
a legitimate return and the guard rejects proposals when no probe
tasks are available.
"""

import copy
from collections.abc import Awaitable, Callable

from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.config import ShadowEvaluationConfig
from synthorg.observability import get_logger

logger = get_logger(__name__)


TaskSampler = Callable[["NotBlankStr", int], Awaitable[tuple["Task", ...]]]
"""Sampler signature: ``(agent_id, sample_size) -> recent tasks``."""


class ConfiguredShadowTaskProvider:
    """Returns the operator-curated probe suite (slice to ``sample_size``).

    Tasks are deep-copied at construction and again on every ``sample``
    call so runner-side mutations cannot leak across passes or back into
    the ``ShadowEvaluationConfig.probe_tasks`` tuple.
    """

    def __init__(self, config: ShadowEvaluationConfig) -> None:
        """Store a deep copy of the configured probe tasks."""
        self._probe_tasks: tuple[Task, ...] = tuple(
            copy.deepcopy(task) for task in config.probe_tasks
        )

    @property
    def name(self) -> str:
        """Strategy name."""
        return "configured"

    async def sample(
        self,
        *,
        agent_id: NotBlankStr,  # noqa: ARG002
        sample_size: int,
    ) -> tuple[Task, ...]:
        """Return up to ``sample_size`` deep-copied curated tasks."""
        if sample_size <= 0:
            return ()
        return tuple(copy.deepcopy(task) for task in self._probe_tasks[:sample_size])


class RecentTaskHistoryProvider:
    """Samples recent COMPLETED tasks for the agent via a pluggable callable.

    The ``sampler`` returns tasks newest-first; the provider trusts it
    to filter for COMPLETED status.  Empty returns are allowed and cause
    the guard to reject the proposal.  Sampled tasks are deep-copied at
    the boundary so runner-side mutations do not leak back to the
    sampler's storage.
    """

    def __init__(self, sampler: TaskSampler) -> None:
        """Store the callable sampler (injected at build time)."""
        self._sampler = sampler

    @property
    def name(self) -> str:
        """Strategy name."""
        return "recent_history"

    async def sample(
        self,
        *,
        agent_id: NotBlankStr,
        sample_size: int,
    ) -> tuple[Task, ...]:
        """Delegate to the sampler; clamp and deep-copy the result.

        Returns:
            Tuple of up to ``sample_size`` deep-copied :class:`Task`
            objects from the sampler; ``()`` when ``sample_size``
            is non-positive.
        """
        if sample_size <= 0:
            return ()
        tasks = await self._sampler(agent_id, sample_size)
        return tuple(copy.deepcopy(task) for task in tasks[:sample_size])
