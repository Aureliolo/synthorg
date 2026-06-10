# module-kind: service
"""Orchestrate receipt build, persistence, rendering, and validation.

:class:`DeliverableReceiptService` is the feature's public seam. On a
completed deliverable it derives the run's ``execution_id`` from the
flight recorder, finds the deliverable's living document, builds the
receipt, persists it, and projects a human-readable section into the
doc. It also serves reads and validation for the REST controller.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.deliverable_receipts.builder import ReceiptBuilder
from synthorg.deliverable_receipts.errors import DeliverableReceiptNotFoundError
from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptValidationResult,
)
from synthorg.deliverable_receipts.renderer import ReceiptRenderer
from synthorg.deliverable_receipts.validator import ReceiptValidator
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.errors import DocNotFoundError
from synthorg.docs_engine.service import DocsService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    RECEIPT_BUILT,
    RECEIPT_RENDERED,
    RECEIPT_VALIDATED,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
    DeliverableReceiptRepository,
)
from synthorg.persistence.docs_protocol import DocsFilterSpec, DocsRepository
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)

logger = get_logger(__name__)

#: Maximum deliverable docs scanned when resolving a task's deliverable.
_DELIVERABLE_SCAN_LIMIT: Final[int] = 100


class DeliverableReceiptService:
    """Build, store, render, and validate deliverable receipts."""

    def __init__(  # noqa: PLR0913 -- composition seam wiring every collaborator
        self,
        *,
        receipts: DeliverableReceiptRepository,
        builder: ReceiptBuilder,
        validator: ReceiptValidator,
        renderer: ReceiptRenderer,
        docs: DocsRepository,
        docs_service: DocsService,
        flight_recorder: FlightRecorderFrameRepository,
    ) -> None:
        self._receipts = receipts
        self._builder = builder
        self._validator = validator
        self._renderer = renderer
        self._docs = docs
        self._docs_service = docs_service
        self._flight_recorder = flight_recorder

    async def build_and_store(self, *, task: Task) -> DeliverableReceipt | None:
        """Build, persist, and render the receipt for a completed task.

        Returns:
            The persisted receipt, or ``None`` when the run was never
            recorded or the task produced no deliverable document.
        """
        execution_id = await self._resolve_execution_id(task)
        if execution_id is None:
            return None
        slug = await self._resolve_deliverable_slug(task)
        if slug is None:
            return None
        receipt = await self._builder.build(
            task=task,
            execution_id=execution_id,
            deliverable_doc_slug=slug,
        )
        await self._receipts.save(receipt)
        logger.info(
            RECEIPT_BUILT,
            task_id=str(task.id),
            execution_id=execution_id,
            deliverable_doc_slug=slug,
            source_count=len(receipt.sources),
            test_count=len(receipt.tests),
            has_red_team=receipt.red_team is not None,
            has_cassette=receipt.cassette is not None,
        )
        await self._render(receipt)
        return receipt

    async def _render(self, receipt: DeliverableReceipt) -> None:
        """Project the receipt into its deliverable doc (best-effort)."""
        try:
            await self._renderer.render_into_doc(receipt=receipt)
        except DocNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RECEIPT_RENDERED,
                task_id=receipt.task_id,
                deliverable_doc_slug=receipt.deliverable_doc_slug,
                rendered=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(
            RECEIPT_RENDERED,
            task_id=receipt.task_id,
            deliverable_doc_slug=receipt.deliverable_doc_slug,
            rendered=True,
        )

    async def get(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
    ) -> DeliverableReceipt | None:
        """Return the receipt for a deliverable doc, or ``None``.

        Returns:
            The receipt whose ``deliverable_doc_slug`` matches, else
            ``None``.
        """
        rows = await self._receipts.query(
            DeliverableReceiptFilterSpec(
                project_id=project_id,
                deliverable_doc_slug=slug,
            ),
            limit=1,
        )
        return rows[0] if rows else None

    async def validate(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
    ) -> ReceiptValidationResult:
        """Validate the receipt for a deliverable doc.

        Returns:
            The :class:`ReceiptValidationResult`.

        Raises:
            DeliverableReceiptNotFoundError: When no receipt exists for
                the deliverable.
        """
        receipt = await self.get(project_id=project_id, slug=slug)
        if receipt is None:
            msg = f"no receipt for deliverable {slug!r} in project {project_id!r}"
            raise DeliverableReceiptNotFoundError(msg)
        result = await self._validator.validate(receipt)
        logger.info(
            RECEIPT_VALIDATED,
            project_id=project_id,
            deliverable_doc_slug=slug,
            valid=result.valid,
            error_count=len(result.errors),
        )
        return result

    async def _resolve_execution_id(self, task: Task) -> NotBlankStr | None:
        """Recover the run's execution id from the flight recorder.

        Returns:
            The latest recorded execution id for the task, or ``None``
            when no frame was ever recorded for it.
        """
        aggregate = await self._flight_recorder.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=str(task.id)),
        )
        return aggregate.latest_execution_id

    async def _resolve_deliverable_slug(self, task: Task) -> NotBlankStr | None:
        """Find the deliverable doc whose related tasks include this task.

        Scans the project's deliverable docs recency-first and reads each
        to inspect its ``related_task_ids`` (the metadata row does not
        carry them).

        Returns:
            The first matching deliverable doc's slug, or ``None`` when
            the task has no deliverable document.
        """
        metas = await self._docs.query(
            DocsFilterSpec(project_id=task.project, doc_type=DocType.DELIVERABLE),
            limit=_DELIVERABLE_SCAN_LIMIT,
        )
        for meta in metas:
            try:
                doc = await self._docs_service.read_doc(
                    project_id=task.project,
                    slug=meta.slug,
                )
            except DocNotFoundError:
                continue
            if str(task.id) in doc.related_task_ids:
                return meta.slug
        return None
