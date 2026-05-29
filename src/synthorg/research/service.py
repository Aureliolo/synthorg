"""Research orchestration: brief -> plan -> retrieve -> triage -> dedupe -> synthesise.

:class:`ResearchService` drives the full pipeline and persists a
:class:`ResearchRun` record. The run is the single source of truth for
retrieval, so a recorded run replays deterministically: the cassette
provider replays the planner / triage / synthesis LLM calls and
:class:`ReplayRetrievalSource` serves the recorded items.

A single source failing does not abort the run: the source is logged and
skipped, and the pipeline continues with the remaining candidates.
"""

import asyncio
from itertools import chain
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ResearchRunStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.research import (
    RESEARCH_RUN_COMPLETED,
    RESEARCH_RUN_DEDUPLICATED,
    RESEARCH_RUN_FAILED,
    RESEARCH_RUN_PLANNED,
    RESEARCH_RUN_RETRIEVED,
    RESEARCH_RUN_STARTED,
    RESEARCH_RUN_SYNTHESISED,
    RESEARCH_RUN_TRIAGED,
    RESEARCH_SOURCE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT
from synthorg.research.errors import (
    ResearchBudgetExceededError,
    ResearchError,
    ResearchRunError,
)
from synthorg.research.models import (
    ResearchRun,
    RetrievedItem,
    SourceCredibility,
    SubQuery,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.core.enums import ResearchSourceType
    from synthorg.persistence.research_protocol import (
        ResearchRunFilter,
        ResearchRunRepository,
    )
    from synthorg.research.models import ResearchBrief
    from synthorg.research.planning.protocol import QueryPlanner
    from synthorg.research.retrieval.protocol import Deduplicator, RetrievalSource
    from synthorg.research.synthesis.protocol import Synthesizer
    from synthorg.research.triage.protocol import CredibilityTriage

logger = get_logger(__name__)


class ResearchService:
    """Orchestrates the multi-source research pipeline for one brief."""

    def __init__(  # noqa: PLR0913 -- injected pipeline collaborators
        self,
        *,
        planner: QueryPlanner,
        sources: dict[ResearchSourceType, RetrievalSource],
        triage: CredibilityTriage,
        deduplicator: Deduplicator,
        synthesizer: Synthesizer,
        runs_repo: ResearchRunRepository,
        clock: Clock | None = None,
        per_query_limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> None:
        self._planner = planner
        self._sources = sources
        self._triage = triage
        self._deduplicator = deduplicator
        self._synthesizer = synthesizer
        self._runs_repo = runs_repo
        self._clock = clock if clock is not None else SystemClock()
        self._per_query_limit = per_query_limit

    async def run(
        self,
        brief: ResearchBrief,
        *,
        run_id: NotBlankStr,
        created_by: NotBlankStr,
    ) -> ResearchRun:
        """Execute the pipeline and return the persisted, terminal run.

        Args:
            brief: The research brief to fulfil.
            run_id: Caller-supplied stable run identifier (keeps a recorded
                run replayable byte-for-byte).
            created_by: Agent or operator that initiated the run.

        Returns:
            The persisted :class:`ResearchRun` in ``COMPLETED`` state.

        Raises:
            ResearchBudgetExceededError: If the run breaches its cost or
                wall-clock ceiling; the run row is persisted ``FAILED``.
            ResearchError: If any pipeline stage fails; the run row is
                persisted in ``FAILED`` state before the error propagates.
            ResearchRunError: Wrapping any other unexpected failure after
                the run row is persisted ``FAILED``.
        """
        started_at = self._clock.now()
        run = ResearchRun(
            run_id=run_id,
            brief_id=brief.brief_id,
            project_id=brief.project_id,
            status=ResearchRunStatus.PLANNING,
            brief=brief,
            created_by=created_by,
            created_at=started_at,
        )
        await self._runs_repo.save(run)
        logger.info(RESEARCH_RUN_STARTED, run_id=run_id, brief_id=brief.brief_id)
        try:
            async with asyncio.timeout(brief.max_wall_clock_seconds):
                return await self._execute(run, brief, started_at)
        except TimeoutError as exc:
            budget = ResearchBudgetExceededError(
                f"research run exceeded wall-clock budget of "
                f"{brief.max_wall_clock_seconds}s"
            )
            await self._fail(run, budget)
            raise budget from exc
        except ResearchError as exc:
            await self._fail(run, exc)
            raise
        except Exception as exc:
            reraise_critical(exc)
            await self._fail(run, exc)
            msg = "Research run failed"
            raise ResearchRunError(msg) from exc

    async def get_run(self, run_id: NotBlankStr) -> ResearchRun | None:
        """Return a persisted run by id, or ``None`` when absent."""
        return await self._runs_repo.get(run_id)

    async def list_runs(
        self,
        filter_spec: ResearchRunFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """Return persisted runs matching *filter_spec*, most-recent first."""
        return await self._runs_repo.query(filter_spec, limit=limit, offset=offset)

    async def _execute(
        self,
        run: ResearchRun,
        brief: ResearchBrief,
        started_at: datetime,
    ) -> ResearchRun:
        """Run the pipeline stages and persist the completed run.

        Returns:
            The persisted ``ResearchRun`` in ``COMPLETED`` state, carrying
            the plan, retrieved items, verdicts, report, and accrued cost.
        """
        total_cost = 0.0
        plan, plan_cost = await self._planner.plan(brief)
        total_cost += plan_cost
        self._enforce_cost_budget(total_cost, brief)
        logger.info(
            RESEARCH_RUN_PLANNED,
            run_id=run.run_id,
            sub_queries=len(plan.sub_queries),
        )

        items = await self._retrieve(brief, plan.sub_queries)
        consulted = len(items)
        logger.info(RESEARCH_RUN_RETRIEVED, run_id=run.run_id, candidates=consulted)

        verdicts, triage_cost = await self._triage.triage(items, brief=brief)
        total_cost += triage_cost
        self._enforce_cost_budget(total_cost, brief)
        retained = self._retain(items, verdicts)
        logger.info(
            RESEARCH_RUN_TRIAGED,
            run_id=run.run_id,
            retained=len(retained),
        )

        deduped = await self._deduplicator.dedupe(retained)
        logger.info(
            RESEARCH_RUN_DEDUPLICATED,
            run_id=run.run_id,
            kept=len(deduped),
        )

        report, synth_cost = await self._synthesizer.synthesize(
            brief, plan, deduped, sources_consulted=consulted
        )
        total_cost += synth_cost
        self._enforce_cost_budget(total_cost, brief)
        logger.info(
            RESEARCH_RUN_SYNTHESISED,
            run_id=run.run_id,
            claims=len(report.claims),
        )

        completed_at = self._clock.now()
        elapsed = (completed_at - started_at).total_seconds()
        completed = run.model_copy(
            update={
                "status": ResearchRunStatus.COMPLETED,
                "query_plan": plan,
                "retrieved_items": items,
                "credibility": verdicts,
                "report": report,
                "cost": total_cost,
                "wall_clock_seconds": max(0.0, elapsed),
                "completed_at": completed_at,
            }
        )
        await self._runs_repo.save(completed)
        logger.info(
            RESEARCH_RUN_COMPLETED,
            run_id=run.run_id,
            brief_id=run.brief_id,
            cost=total_cost,
        )
        return completed

    async def _retrieve(
        self,
        brief: ResearchBrief,
        sub_queries: tuple[SubQuery, ...],
    ) -> tuple[RetrievedItem, ...]:
        """Fan out retrieval across sources, isolating per-source failures.

        Returns:
            All retrieved items across the sub-queries, flattened in
            completion order.
        """
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._safe_retrieve(brief, sub_query))
                    for sub_query in sub_queries
                ]
        except BaseExceptionGroup as exc_group:
            for inner in exc_group.exceptions:
                if isinstance(inner, (MemoryError, RecursionError)):
                    raise inner from exc_group
            raise
        return tuple(chain.from_iterable(task.result() for task in tasks))

    async def _safe_retrieve(
        self,
        brief: ResearchBrief,
        sub_query: SubQuery,
    ) -> tuple[RetrievedItem, ...]:
        """Retrieve for one sub-query; never raise to the TaskGroup.

        Returns:
            The items retrieved for the sub-query, or an empty tuple when
            the source is unconfigured or its retrieve call fails.
        """
        source = self._sources.get(sub_query.source_type)
        if source is None:
            logger.warning(
                RESEARCH_SOURCE_FAILED,
                source_type=sub_query.source_type.value,
                reason="source_not_configured",
            )
            return ()
        try:
            return await source.retrieve(
                sub_query,
                project_id=brief.project_id,
                limit=self._per_query_limit,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                RESEARCH_SOURCE_FAILED,
                source_type=sub_query.source_type.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    @staticmethod
    def _enforce_cost_budget(total_cost: float, brief: ResearchBrief) -> None:
        """Raise if accumulated LLM cost has breached the brief's ceiling.

        Raises:
            ResearchBudgetExceededError: When ``total_cost`` exceeds the
                brief's ``max_cost``.
        """
        if total_cost > brief.max_cost:
            msg = f"research run cost {total_cost} exceeded budget of {brief.max_cost}"
            raise ResearchBudgetExceededError(msg)

    @staticmethod
    def _retain(
        items: tuple[RetrievedItem, ...],
        verdicts: tuple[SourceCredibility, ...],
    ) -> tuple[RetrievedItem, ...]:
        """Keep only items whose credibility verdict passed the threshold.

        Returns:
            The items whose credibility verdict passed, preserving order.
        """
        passed = {v.ref_id for v in verdicts if v.passed}
        return tuple(item for item in items if item.ref_id in passed)

    async def _fail(self, run: ResearchRun, exc: Exception) -> None:
        """Persist the run in FAILED state with a safe error description."""
        failed = run.model_copy(
            update={
                "status": ResearchRunStatus.FAILED,
                "error": NotBlankStr(safe_error_description(exc) or "research failed"),
                "completed_at": self._clock.now(),
            }
        )
        await self._runs_repo.save(failed)
        logger.warning(
            RESEARCH_RUN_FAILED,
            run_id=run.run_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
