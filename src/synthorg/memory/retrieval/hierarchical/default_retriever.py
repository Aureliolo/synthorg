"""Default hierarchical retriever -- supervisor-worker orchestration.

Implements the 4-phase pipeline: Route -> Execute -> Merge -> Retry.
"""

import asyncio
import builtins
from collections.abc import Mapping
from types import MappingProxyType

from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.retrieval.hierarchical.supervisor import (
    SupervisorRouter,
)
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
)
from synthorg.memory.retrieval.protocol import RetrievalWorker
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_HIERARCHICAL_COMPLETE,
    MEMORY_HIERARCHICAL_MERGE,
    MEMORY_HIERARCHICAL_RETRY,
    MEMORY_HIERARCHICAL_WORKER_FAILED,
)

logger = get_logger(__name__)


def _deduplicate_candidates(
    candidates: tuple[RetrievalCandidate, ...],
    *,
    max_results: int,
) -> tuple[RetrievalCandidate, ...]:
    """Deduplicate by entry.id, keeping highest combined_score.

    Returns:
        Tuple of ``RetrievalCandidate``.
    """
    seen: dict[str, RetrievalCandidate] = {}
    for c in candidates:
        existing = seen.get(c.entry.id)
        if existing is None or c.combined_score > existing.combined_score:
            seen[c.entry.id] = c
    sorted_candidates = sorted(
        seen.values(),
        key=lambda c: c.combined_score,
        reverse=True,
    )
    return tuple(sorted_candidates[:max_results])


class DefaultHierarchicalRetriever:
    """Supervisor-worker hierarchical retriever.

    Pipeline:
    1. **Route**: supervisor decides which workers to invoke.
    2. **Execute**: run selected workers in parallel.
    3. **Merge**: deduplicate and sort candidates.
    4. **Retry**: if enabled and result quality is low.

    Args:
        supervisor: LLM-based routing supervisor.
        workers: Mapping from worker name to worker instance.
        config: Retrieval pipeline configuration.
    """

    def __init__(
        self,
        *,
        supervisor: SupervisorRouter,
        workers: Mapping[str, RetrievalWorker],
        config: MemoryRetrievalConfig,
    ) -> None:
        self._supervisor = supervisor
        # Freeze the worker registry so routing cannot be mutated
        # after construction.  Deepcopy is skipped because
        # ``RetrievalWorker`` instances hold live backend references.
        self._workers: Mapping[str, RetrievalWorker] = MappingProxyType(
            dict(workers),
        )
        self._config = config

    async def retrieve(
        self,
        query: RetrievalQuery,
    ) -> FinalRetrievalResult:
        """Execute the full hierarchical retrieval pipeline.

        Returns:
            Result of type ``FinalRetrievalResult``.
        """
        routing = await self._supervisor.route(query)
        selected = list(
            dedupe_preserving_order(
                name for name in routing.selected_workers if name in self._workers
            )
        )
        if not selected:
            selected = ["semantic"] if "semantic" in self._workers else []
        if not selected:
            return FinalRetrievalResult()

        worker_results = await self._execute_workers(selected, query)

        all_candidates: list[RetrievalCandidate] = []
        for wr in worker_results:
            all_candidates.extend(wr.candidates)
        merged = _deduplicate_candidates(
            tuple(all_candidates),
            max_results=query.max_results,
        )

        result = FinalRetrievalResult(
            candidates=merged,
            worker_results=tuple(worker_results),
        )

        logger.debug(
            MEMORY_HIERARCHICAL_MERGE,
            total_raw_candidates=len(all_candidates),
            deduped_count=len(merged),
            workers_invoked=selected,
        )

        retries = 0
        current_query = query
        last_attempt: tuple[str, tuple[str, ...]] = (
            query.text,
            tuple(selected),
        )
        if self._supervisor.reflective_retry_enabled:
            max_retries = self._supervisor.max_retry_count
            while retries < max_retries:
                correction = await self._supervisor.evaluate_for_retry(
                    current_query,
                    result,
                )
                if correction is None:
                    break
                if correction.alternative_strategy == "skip":
                    logger.info(
                        MEMORY_HIERARCHICAL_RETRY,
                        action="skip",
                        retry_count=retries + 1,
                        reason=correction.reason,
                    )
                    break
                proposed_query = (
                    correction.corrected_query
                    if correction.corrected_query is not None
                    else current_query
                )
                proposed_workers = self._resolve_retry_workers(
                    correction.alternative_strategy,
                    selected,
                )
                proposed_attempt: tuple[str, tuple[str, ...]] = (
                    proposed_query.text,
                    tuple(proposed_workers),
                )
                if proposed_attempt == last_attempt:
                    logger.info(
                        MEMORY_HIERARCHICAL_RETRY,
                        action="no-op",
                        retry_count=retries + 1,
                        reason="supervisor proposed identical (query, workers)",
                    )
                    break
                retries += 1
                current_query = proposed_query
                retry_query = current_query
                retry_workers = proposed_workers
                last_attempt = proposed_attempt
                logger.info(
                    MEMORY_HIERARCHICAL_RETRY,
                    action="executing",
                    retry_count=retries,
                    workers=retry_workers,
                    reason=correction.reason,
                )
                retry_results = await self._execute_workers(
                    retry_workers,
                    retry_query,
                )
                # Design: we intentionally accumulate all attempts
                # (initial + every retry) in ``all_candidates`` and
                # ``worker_results`` rather than replacing stale
                # output.  ``_deduplicate_candidates`` dedups by
                # ``entry.id`` and keeps the highest
                # ``combined_score``, so the final ``candidates``
                # surface only the best-scored hit per entry.
                # Accumulating preserves the full per-attempt audit
                # trail in ``worker_results`` for observability.
                for wr in retry_results:
                    all_candidates.extend(wr.candidates)
                merged = _deduplicate_candidates(
                    tuple(all_candidates),
                    max_results=query.max_results,
                )
                all_worker_results = list(result.worker_results)
                all_worker_results.extend(retry_results)
                result = FinalRetrievalResult(
                    candidates=merged,
                    worker_results=tuple(all_worker_results),
                    retries_performed=retries,
                )

        logger.info(
            MEMORY_HIERARCHICAL_COMPLETE,
            candidate_count=len(result.candidates),
            worker_count=len(result.worker_results),
            retries=retries,
        )
        return result

    async def _execute_workers(
        self,
        worker_names: list[str],
        query: RetrievalQuery,
    ) -> list[RetrievalResult]:
        """Execute named workers in parallel with error isolation.

        Each worker failure is independently logged at WARNING via
        ``MEMORY_HIERARCHICAL_WORKER_FAILED`` and converted to an empty
        :class:`RetrievalResult` whose ``error`` field carries the
        scrubbed description. Downstream aggregation in :meth:`retrieve`
        merges per-worker candidate tuples and tolerates an empty list
        on any individual worker; the design intent is partial-result
        retrieval (a vector-store outage drops the semantic worker
        but leaves episodic / procedural matches alive) rather than
        all-or-nothing
        propagation. The WARNING log is the operator-visible signal;
        the returned ``error`` field is for callers that *do* want to
        branch on a per-worker failure.

        Returns:
            List of ``RetrievalResult``.
        """

        async def _run_worker(name: str) -> RetrievalResult:
            """Run worker.

            Returns:
                Result of type ``RetrievalResult``.

            Raises:
                MemoryError: If the related operation fails.
                RecursionError: If the related operation fails.
            """
            worker = self._workers[name]
            try:
                return await worker.retrieve(query)
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    MEMORY_HIERARCHICAL_WORKER_FAILED,
                    worker=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return RetrievalResult(
                    worker_name=name,
                    error=safe_error_description(exc),
                )

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_run_worker(n)) for n in worker_names]

        return [t.result() for t in tasks]

    def _resolve_retry_workers(
        self,
        alternative_strategy: str | None,
        original_workers: list[str],
    ) -> list[str]:
        """Resolve which workers to use for a retry.

        Returns:
            List of ``str``.
        """
        if alternative_strategy == "semantic_only":
            return ["semantic"] if "semantic" in self._workers else original_workers
        if alternative_strategy == "episodic_only":
            return ["episodic"] if "episodic" in self._workers else original_workers
        return original_workers
