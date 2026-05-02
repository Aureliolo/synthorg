"""Training service orchestrator."""

import asyncio
import copy
from collections import OrderedDict
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.hr.training.models import (
    ContentType,
    TrainingApprovalHandle,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)
from synthorg.memory.errors import MemoryError as _MemoryError
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_TRAINING_SESSION_INVALID_REQUEST,
    HR_TRAINING_SESSION_LISTED,
    HR_TRAINING_SESSION_RECORD_FAILED,
    HR_TRAINING_SESSION_RECORDED,
)
from synthorg.observability.events.training import (
    HR_TRAINING_CURATION_FAILED,
    HR_TRAINING_EXTRACTION_FAILED,
    HR_TRAINING_GUARD_EVALUATION,
    HR_TRAINING_GUARD_FAILED,
    HR_TRAINING_ITEMS_EXTRACTED,
    HR_TRAINING_PLAN_EXECUTED,
    HR_TRAINING_PLAN_FAILED,
    HR_TRAINING_PLAN_IDEMPOTENT,
    HR_TRAINING_PLAN_STATUS_TRANSITIONED,
    HR_TRAINING_REVIEW_PENDING,
    HR_TRAINING_SKIPPED,
    HR_TRAINING_STORE_FAILED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.hr.training.protocol import (
        ContentExtractor,
        CurationStrategy,
        SourceSelector,
        TrainingGuard,
    )
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

# Map content types to memory categories for storage.
_CONTENT_TYPE_TO_CATEGORY: dict[ContentType, MemoryCategory] = {
    ContentType.PROCEDURAL: MemoryCategory.PROCEDURAL,
    ContentType.SEMANTIC: MemoryCategory.SEMANTIC,
    ContentType.TOOL_PATTERNS: MemoryCategory.PROCEDURAL,
}

# Internal type alias for curated items map passed through pipeline.
_CuratedMap = dict[ContentType, tuple[TrainingItem, ...]]

# Cap for the in-memory session store used by the MCP read-side.
# FIFO eviction keeps the store bounded without a background sweeper.
# Durable session history lives in a future repository (tracked
# alongside the fine-tune run repo); the MCP surface intentionally
# surfaces only the recent tail today.
_SESSION_STORE_MAX: int = 500


class TrainingService:
    """Training pipeline orchestrator.

    Executes the full training flow: source resolution, parallel
    extraction + curation, sequential guard chain, and memory
    storage.  Idempotency is enforced per ``plan.id`` within this
    service instance: concurrent or repeated ``execute()`` calls
    with the same plan id see a no-op after the first successful
    run.

    Args:
        selector: Source agent selector.
        extractors: Content extractors keyed by content type.
        curation: Curation strategy.
        guards: Guard chain (applied in order).
        memory_backend: Memory backend for storing items.
        training_namespace: Memory namespace for stored items.
        training_tags: Default tags for stored items.
    """

    # ``__slots__`` was evaluated but intentionally not added: the
    # session test harness in ``tests/unit/hr/training/test_service_sessions.py``
    # monkey-patches ``_execute_locked`` onto live instances so
    # ``start_session`` sees a synthetic pipeline result without
    # rebuilding the full dependency graph. ``__slots__`` would block
    # that per-instance assignment with ``AttributeError``, and patching
    # via ``type(service).__dict__`` would mutate the class for every
    # concurrent test worker. The memory saving is negligible for a
    # service instance spawned once per app state, so we keep the
    # flexible instance dict.

    def __init__(  # noqa: PLR0913
        self,
        *,
        selector: SourceSelector,
        extractors: Mapping[ContentType, ContentExtractor],
        curation: CurationStrategy,
        guards: tuple[TrainingGuard, ...],
        memory_backend: MemoryBackend,
        training_namespace: str = "training",
        training_tags: tuple[str, ...] = ("learned_from_seniors",),
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._selector = selector
        # Deep copy + freeze so external mutation of the caller-owned
        # mapping cannot alter pipeline behavior mid-flight.
        self._extractors: Mapping[ContentType, ContentExtractor] = MappingProxyType(
            copy.deepcopy(dict(extractors)),
        )
        self._curation = curation
        self._guards = guards
        self._memory_backend = memory_backend
        self._training_namespace = training_namespace
        self._training_tags = training_tags
        # Optional resolver for the runtime ``hr.training_enabled``
        # kill switch.  When wired, ``execute`` reads the flag at the
        # entry point so an operator can pause ingestion without
        # restart; without a resolver the registry default (``True``)
        # applies.
        self._config_resolver = config_resolver
        self._executed_plan_ids: set[str] = set()
        self._idempotency_lock = asyncio.Lock()
        self._sessions: OrderedDict[str, TrainingPlan] = OrderedDict()
        self._session_lock = asyncio.Lock()

    async def execute(self, plan: TrainingPlan) -> TrainingResult:
        """Execute the full training pipeline.

        Idempotent by ``plan.id``: re-running the same plan after a
        successful execution returns an empty result without touching
        the memory backend.  Concurrent calls are serialized on the
        idempotency lock so exactly one caller performs the run.

        Gated by ``hr.training_enabled``: when ``False`` the call
        short-circuits before idempotency / lock acquisition, so an
        operator can pause training without tearing down the
        scheduler or rewriting plans.

        Args:
            plan: Training plan to execute.

        Returns:
            Training result with pipeline metrics.
        """
        enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="hr",
            key="training_enabled",
            fallback=True,
        )
        if not enabled:
            logger.info(
                HR_TRAINING_SKIPPED,
                plan_id=plan.id,
                reason="training_disabled_by_setting",
            )
            now = datetime.now(UTC)
            return TrainingResult(
                plan_id=plan.id,
                new_agent_id=plan.new_agent_id,
                started_at=now,
                completed_at=now,
            )
        result, _ran = await self._execute_locked(plan)
        return result

    async def _execute_locked(
        self,
        plan: TrainingPlan,
    ) -> tuple[TrainingResult, bool]:
        """Internal: run pipeline if needed, report whether work ran.

        Returns ``(result, ran_pipeline)`` so :meth:`start_session`
        can make a race-free decision about whether to record an
        ``EXECUTED`` session. Two concurrent callers of
        :meth:`start_session` both enter the idempotency check before
        either runs the pipeline; only the caller that actually
        acquires the lock and runs gets ``ran_pipeline=True``, so
        only that caller writes the terminal ``EXECUTED`` session.
        """
        started_at = datetime.now(UTC)

        # Pre-flight short-circuits do not require the lock.
        if plan.status == TrainingPlanStatus.EXECUTED:
            logger.info(
                HR_TRAINING_PLAN_IDEMPOTENT,
                plan_id=str(plan.id),
                reason="status_executed",
            )
            return self._empty_result(plan, started_at), False

        if plan.skip_training:
            logger.info(
                HR_TRAINING_SKIPPED,
                plan_id=str(plan.id),
            )
            return self._empty_result(plan, started_at), False

        async with self._idempotency_lock:
            if str(plan.id) in self._executed_plan_ids:
                logger.info(
                    HR_TRAINING_PLAN_IDEMPOTENT,
                    plan_id=str(plan.id),
                    reason="already_executed_in_service",
                )
                return self._empty_result(plan, started_at), False

            try:
                result = await self._run_pipeline(plan, started_at)
            except Exception as exc:
                # Prefer ``warning`` + ``safe_error_description`` over
                # ``logger.exception(..., error=str(exc))`` so
                # ``str(exc)`` on provider / memory errors doesn't
                # land credential text in the traceback-bearing
                # frame-locals.
                logger.warning(
                    HR_TRAINING_PLAN_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            self._executed_plan_ids.add(str(plan.id))
            return result, True

    async def start_session(self, plan: TrainingPlan) -> TrainingResult:
        """Execute *plan* and record the session on the in-memory store.

        Thin wrapper around :meth:`execute` that additionally tracks
        the plan's terminal status (``EXECUTED`` / ``FAILED``) in the
        service's session history so the MCP read-side
        (:meth:`list_sessions` + :meth:`get_session`) can surface
        recent training activity without reaching into an external
        persistence layer.

        Args:
            plan: Training plan to execute.

        Returns:
            The :class:`TrainingResult` produced by :meth:`execute`.

        Raises:
            Exception: Any exception raised by :meth:`execute` is
                re-raised after the session is recorded as
                ``FAILED``.
        """
        # Use ``_execute_locked`` rather than ``execute`` so the
        # "did pipeline work actually run?" answer is reported from
        # INSIDE the idempotency lock. A pre-call check on
        # ``_executed_plan_ids`` would race: concurrent callers both
        # observe the id missing, both reach ``execute``, but only
        # one acquires the lock and runs -- the other sees the id in
        # the set and returns an empty result. Using the locked
        # helper's ``ran_pipeline`` flag makes the record decision
        # race-free.
        #
        # Entry-call record runs under the session lock and may raise
        # if the store is corrupted or the lock is contended. A failure
        # here must not leave the pipeline un-run; log + continue so
        # the execute path still attempts the work and terminal status
        # (FAILED/EXECUTED) gets recorded via the branches below.
        #
        # Skip the entry write when ``plan.id`` already has a terminal
        # record (EXECUTED / FAILED). A retry with a PENDING plan
        # carrying the same id would otherwise overwrite that
        # terminal record with its pre-execution state, and the
        # ``if not ran_pipeline: return`` branch below would then
        # leave ``list_sessions`` / ``get_session`` observing a
        # downgraded status for an already-finished session.
        if not await self._has_terminal_session(str(plan.id)):
            try:
                await self._record_session(plan)
            except Exception as exc:
                logger.warning(
                    HR_TRAINING_SESSION_RECORD_FAILED,
                    plan_id=str(plan.id),
                    stage="entry",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        try:
            result, ran_pipeline = await self._execute_locked(plan)
        except Exception:
            failed = plan.model_copy(
                update={
                    "status": TrainingPlanStatus.FAILED,
                    "executed_at": datetime.now(UTC),
                },
            )
            try:
                await self._record_session(failed)
            except Exception as exc:
                logger.warning(
                    HR_TRAINING_SESSION_RECORD_FAILED,
                    plan_id=str(failed.id),
                    stage="failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            else:
                logger.info(
                    HR_TRAINING_PLAN_STATUS_TRANSITIONED,
                    plan_id=str(failed.id),
                    from_status=plan.status.value,
                    to_status=TrainingPlanStatus.FAILED.value,
                )
            raise
        if not ran_pipeline:
            return result
        executed = plan.model_copy(
            update={
                "status": TrainingPlanStatus.EXECUTED,
                "executed_at": result.completed_at,
            },
        )
        try:
            await self._record_session(executed)
        except Exception as exc:
            logger.warning(
                HR_TRAINING_SESSION_RECORD_FAILED,
                plan_id=str(executed.id),
                stage="executed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            logger.info(
                HR_TRAINING_PLAN_STATUS_TRANSITIONED,
                plan_id=str(executed.id),
                from_status=plan.status.value,
                to_status=TrainingPlanStatus.EXECUTED.value,
            )
        return result

    async def list_sessions(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[TrainingPlan, ...], int]:
        """Return a page of recent training sessions + the total count.

        Sessions are newest-first (by insertion / most-recent update
        into the session store).

        Args:
            offset: Page offset (>= 0).
            limit: Page size (> 0).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
        """
        if offset < 0:
            logger.warning(
                HR_TRAINING_SESSION_INVALID_REQUEST,
                param="offset",
                value=offset,
            )
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            logger.warning(
                HR_TRAINING_SESSION_INVALID_REQUEST,
                param="limit",
                value=limit,
            )
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        async with self._session_lock:
            items = tuple(reversed(self._sessions.values()))
            total = len(items)
        page = items[offset : offset + limit]
        logger.debug(
            HR_TRAINING_SESSION_LISTED,
            count=len(page),
            total=total,
            offset=offset,
            limit=limit,
        )
        return page, total

    async def get_session(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Fetch a single session by plan id or ``None`` if absent."""
        async with self._session_lock:
            return self._sessions.get(str(plan_id))

    async def _has_terminal_session(self, plan_id: str) -> bool:
        """Return ``True`` when a session for *plan_id* is already terminal.

        "Terminal" here is any status that should not be downgraded by
        an idempotent re-entry of :meth:`start_session` -- currently
        ``EXECUTED`` and ``FAILED``. A session whose status is still
        ``PENDING`` or ``SKIPPED`` is not terminal; re-entry is
        allowed to overwrite those states.
        """
        async with self._session_lock:
            existing = self._sessions.get(plan_id)
        if existing is None:
            return False
        return existing.status in (
            TrainingPlanStatus.EXECUTED,
            TrainingPlanStatus.FAILED,
        )

    async def _record_session(self, plan: TrainingPlan) -> None:
        """Record *plan* at its current state into the session store.

        Newer records bump an existing entry to the head; FIFO eviction
        keeps the store bounded.
        """
        async with self._session_lock:
            key = str(plan.id)
            self._sessions[key] = plan
            self._sessions.move_to_end(key)
            while len(self._sessions) > _SESSION_STORE_MAX:
                self._sessions.popitem(last=False)
        logger.info(
            HR_TRAINING_SESSION_RECORDED,
            plan_id=str(plan.id),
            status=plan.status.value,
        )

    async def preview(self, plan: TrainingPlan) -> TrainingResult:
        """Dry-run: extract + curate without guards or storage.

        Args:
            plan: Training plan to preview.

        Returns:
            Result with extraction and curation counts only.
        """
        started_at = datetime.now(UTC)
        source_ids = await self._resolve_sources(plan)
        extracted, curated, _curated_items = await self._extract_and_curate(
            plan, source_ids
        )

        return TrainingResult(
            plan_id=plan.id,
            new_agent_id=plan.new_agent_id,
            source_agents_used=source_ids,
            items_extracted=extracted,
            items_after_curation=curated,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    async def _run_pipeline(
        self,
        plan: TrainingPlan,
        started_at: datetime,
    ) -> TrainingResult:
        """Execute extraction, curation, guards, and storage."""
        source_ids = await self._resolve_sources(plan)
        extracted, curated, curated_items = await self._extract_and_curate(
            plan, source_ids
        )
        (
            guarded,
            errors,
            guarded_items,
            approval_id,
            pending_approvals,
        ) = await self._apply_guards(plan, curated_items)
        stored = await self._store_items(plan, guarded_items)

        completed_at = datetime.now(UTC)

        logger.info(
            HR_TRAINING_PLAN_EXECUTED,
            plan_id=str(plan.id),
            source_count=len(source_ids),
            extracted_total=sum(c for _, c in extracted),
            stored_total=sum(c for _, c in stored),
            review_pending=bool(pending_approvals),
        )

        return TrainingResult(
            plan_id=plan.id,
            new_agent_id=plan.new_agent_id,
            source_agents_used=source_ids,
            items_extracted=extracted,
            items_after_curation=curated,
            items_after_guards=guarded,
            items_stored=stored,
            approval_item_id=approval_id,
            pending_approvals=pending_approvals,
            review_pending=bool(pending_approvals),
            errors=errors,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _resolve_sources(
        self,
        plan: TrainingPlan,
    ) -> tuple[NotBlankStr, ...]:
        """Resolve source agent IDs."""
        if plan.override_sources:
            return plan.override_sources
        return await self._selector.select(
            new_agent_role=plan.new_agent_role,
            new_agent_level=plan.new_agent_level,
            new_agent_department=plan.new_agent_department,
        )

    async def _extract_and_curate(
        self,
        plan: TrainingPlan,
        source_ids: tuple[NotBlankStr, ...],
    ) -> tuple[
        tuple[tuple[ContentType, int], ...],
        tuple[tuple[ContentType, int], ...],
        _CuratedMap,
    ]:
        """Run extraction + curation in parallel per content type.

        Each content type runs extraction then curation in its own
        task.  Results are returned from each task and merged after
        the TaskGroup completes -- no shared mutable state during
        the parallel phase.  Iteration order is deterministic via
        sorted content type values.
        """
        ordered_types = tuple(
            sorted(plan.enabled_content_types, key=lambda ct: ct.value),
        )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    self._extract_and_curate_one(plan, source_ids, ct),
                )
                for ct in ordered_types
            ]

        extracted_counts: list[tuple[ContentType, int]] = []
        curated_counts: list[tuple[ContentType, int]] = []
        curated_items: _CuratedMap = {}

        for task in tasks:
            result = task.result()
            if result is None:
                continue
            ct, ext_count, cur_count, curated = result
            extracted_counts.append((ct, ext_count))
            curated_counts.append((ct, cur_count))
            curated_items[ct] = curated

        return (
            tuple(extracted_counts),
            tuple(curated_counts),
            curated_items,
        )

    async def _extract_and_curate_one(
        self,
        plan: TrainingPlan,
        source_ids: tuple[NotBlankStr, ...],
        ct: ContentType,
    ) -> tuple[ContentType, int, int, tuple[TrainingItem, ...]] | None:
        """Extract + curate a single content type with error logging."""
        extractor = self._extractors.get(ct)
        if extractor is None:
            msg = (
                f"No extractor configured for content type "
                f"{ct.value!r} (plan {plan.id})"
            )
            raise RuntimeError(msg)

        try:
            items = await extractor.extract(
                source_agent_ids=source_ids,
                new_agent_role=plan.new_agent_role,
                new_agent_level=plan.new_agent_level,
            )
        except Exception as exc:
            # See comment at the top of ``_execute_locked``.
            logger.warning(
                HR_TRAINING_EXTRACTION_FAILED,
                plan_id=str(plan.id),
                content_type=ct.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.debug(
            HR_TRAINING_ITEMS_EXTRACTED,
            content_type=ct.value,
            count=len(items),
        )

        try:
            curated = await self._curation.curate(
                items,
                new_agent_role=plan.new_agent_role,
                new_agent_level=plan.new_agent_level,
                content_type=ct,
            )
        except Exception as exc:
            # See comment at the top of ``_execute_locked``.
            logger.warning(
                HR_TRAINING_CURATION_FAILED,
                plan_id=str(plan.id),
                content_type=ct.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        return ct, len(items), len(curated), curated

    async def _apply_guards(
        self,
        plan: TrainingPlan,
        curated_items: _CuratedMap,
    ) -> tuple[
        tuple[tuple[ContentType, int], ...],
        tuple[str, ...],
        _CuratedMap,
        str | None,
        tuple[TrainingApprovalHandle, ...],
    ]:
        """Apply guard chain sequentially per content type.

        Returns guarded counts, errors, guarded items map, the first
        approval item id (for backwards-compatible callers), and the
        full tuple of pending approval handles so no ID is lost when
        multiple content types trigger review.
        """
        guarded_counts: list[tuple[ContentType, int]] = []
        all_errors: list[str] = []
        guarded_items: _CuratedMap = {}
        approval_handles: list[TrainingApprovalHandle] = []

        for ct in sorted(curated_items.keys(), key=lambda c: c.value):
            items = curated_items[ct]
            current_items, errors, handle = await self._run_guards_for_type(
                plan,
                ct,
                items,
            )
            all_errors.extend(errors)
            if handle is not None:
                approval_handles.append(handle)
            guarded_counts.append((ct, len(current_items)))
            guarded_items[ct] = current_items

        approval_id = (
            str(approval_handles[0].approval_item_id) if approval_handles else None
        )

        if approval_handles:
            logger.info(
                HR_TRAINING_REVIEW_PENDING,
                plan_id=str(plan.id),
                approval_count=len(approval_handles),
                content_types=[h.content_type.value for h in approval_handles],
            )

        return (
            tuple(guarded_counts),
            tuple(all_errors),
            guarded_items,
            approval_id,
            tuple(approval_handles),
        )

    async def _run_guards_for_type(
        self,
        plan: TrainingPlan,
        ct: ContentType,
        items: tuple[TrainingItem, ...],
    ) -> tuple[
        tuple[TrainingItem, ...],
        list[str],
        TrainingApprovalHandle | None,
    ]:
        """Apply the guard chain to a single content type."""
        current_items = items
        errors: list[str] = []
        handle: TrainingApprovalHandle | None = None

        for guard in self._guards:
            try:
                decision: TrainingGuardDecision = await guard.evaluate(
                    current_items,
                    content_type=ct,
                    plan=plan,
                )
            except Exception as exc:
                # See comment at the top of ``_execute_locked``.
                logger.warning(
                    HR_TRAINING_GUARD_FAILED,
                    plan_id=str(plan.id),
                    guard=guard.name,
                    content_type=ct.value,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

            logger.debug(
                HR_TRAINING_GUARD_EVALUATION,
                guard=guard.name,
                content_type=ct.value,
                approved=len(decision.approved_items),
                rejected=decision.rejected_count,
            )

            current_items = decision.approved_items
            errors.extend(decision.rejection_reasons)

            if decision.approval_item_id is not None:
                handle = TrainingApprovalHandle(
                    approval_item_id=decision.approval_item_id,
                    content_type=ct,
                    item_count=decision.rejected_count,
                )

        return current_items, errors, handle

    async def _store_items(
        self,
        plan: TrainingPlan,
        guarded_items: _CuratedMap,
    ) -> tuple[tuple[ContentType, int], ...]:
        """Store approved items to memory backend in parallel per type."""
        stored_counts: list[tuple[ContentType, int]] = []

        for ct in sorted(guarded_items.keys(), key=lambda c: c.value):
            items = guarded_items[ct]
            stored = await self._store_items_for_type(plan, ct, items)
            stored_counts.append((ct, stored))

        return tuple(stored_counts)

    async def _store_items_for_type(
        self,
        plan: TrainingPlan,
        ct: ContentType,
        items: tuple[TrainingItem, ...],
    ) -> int:
        """Store a single content type's items concurrently."""
        if not items:
            return 0

        category = _CONTENT_TYPE_TO_CATEGORY.get(ct, MemoryCategory.PROCEDURAL)

        async with asyncio.TaskGroup() as tg:
            store_tasks = [
                tg.create_task(self._store_one_item(plan, ct, category, item))
                for item in items
            ]

        return sum(1 for task in store_tasks if task.result())

    async def _store_one_item(
        self,
        plan: TrainingPlan,
        ct: ContentType,
        category: MemoryCategory,
        item: TrainingItem,
    ) -> bool:
        """Store a single training item, logging any store failure."""
        tags = (
            *self._training_tags,
            f"training:{plan.id}",
            f"source:{item.source_agent_id}",
        )
        request = MemoryStoreRequest(
            category=category,
            namespace=self._training_namespace,
            content=item.content,
            metadata=MemoryMetadata(
                source=f"training:{plan.id}",
                confidence=item.relevance_score,
                tags=tags,
            ),
        )
        try:
            await self._memory_backend.store(plan.new_agent_id, request)
        except _MemoryError as exc:
            logger.warning(
                HR_TRAINING_STORE_FAILED,
                plan_id=str(plan.id),
                item_id=str(item.id),
                content_type=ct.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return True

    @staticmethod
    def _empty_result(
        plan: TrainingPlan,
        started_at: datetime,
    ) -> TrainingResult:
        """Build an empty result for skipped/idempotent plans."""
        return TrainingResult(
            plan_id=plan.id,
            new_agent_id=plan.new_agent_id,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
