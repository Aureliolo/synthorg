# module-kind: service
"""Assemble a :class:`DeliverableReceipt` from its signal substrate.

The builder gathers the six provenance signals for a completed
deliverable's run: knowledge sources consulted, key decisions and
rationale, aggregate cost, test runs, the red-team snapshot, and the
replayable cassette reference. Each signal is best-effort: a missing
substrate (no brain, no cassette, no red-team report for the run) yields
an empty / ``None`` section rather than failing the build.
"""

import hashlib
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.deliverable_receipts.models import (
    BLOCKING_SEVERITY_FLOOR,
    DeliverableReceipt,
    ReceiptCassetteRef,
    ReceiptDecisionEntry,
    ReceiptRedTeamEntry,
    ReceiptSourceEntry,
    ReceiptTestEntry,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    RECEIPT_CASSETTE_UNAVAILABLE,
    RECEIPT_MIXED_CURRENCY_COST,
    RECEIPT_REDTEAM_UNAVAILABLE,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionPurpose,
)
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageFilterSpec
from synthorg.project_brain.errors import BrainEntryNotFoundError
from synthorg.project_brain.models import BrainEntryKind
from synthorg.security.redteam.errors import RedTeamReportNotFoundError
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamVerdict,
    severity_rank,
)

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.currency import CurrencyCode
    from synthorg.core.clock import Clock
    from synthorg.core.task import Task
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.code_execution_protocol import (
        CodeExecutionRecordRepository,
    )
    from synthorg.persistence.cost_record_protocol import CostRecordRepository
    from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
    from synthorg.persistence.knowledge_usage_protocol import (
        KnowledgeUsageRecordRepository,
    )
    from synthorg.project_brain.service import ProjectBrainService
    from synthorg.providers.cassette.mode import CassetteConfig
    from synthorg.security.redteam.protocol import RedTeamReportRepository

logger = get_logger(__name__)

#: Upper bound on rows pulled per signal when assembling a receipt.
_SIGNAL_QUERY_LIMIT: Final[int] = 1000

_UNRESOLVED_TITLE: Final[str] = "(unresolved source)"


class ReceiptBuilder:
    """Assemble a provenance receipt for one completed deliverable run."""

    def __init__(  # noqa: PLR0913 -- aggregates one collaborator per signal
        self,
        *,
        cost_records: CostRecordRepository,
        knowledge_usage_records: KnowledgeUsageRecordRepository,
        knowledge_sources: KnowledgeSourceRepository,
        code_execution_records: CodeExecutionRecordRepository,
        clock: Clock,
        default_currency: CurrencyCode,
        brain_service: ProjectBrainService | None = None,
        redteam_reports: RedTeamReportRepository | None = None,
        cassette_config: CassetteConfig | None = None,
    ) -> None:
        self._cost_records = cost_records
        self._knowledge_usage_records = knowledge_usage_records
        self._knowledge_sources = knowledge_sources
        self._code_execution_records = code_execution_records
        self._clock = clock
        self._default_currency = default_currency
        self._brain_service = brain_service
        self._redteam_reports = redteam_reports
        self._cassette_config = cassette_config

    async def build(
        self,
        *,
        task: Task,
        execution_id: NotBlankStr,
        deliverable_doc_slug: NotBlankStr,
    ) -> DeliverableReceipt:
        """Assemble the receipt for ``task``'s run ``execution_id``.

        Returns:
            The fully populated (best-effort) :class:`DeliverableReceipt`.
        """
        total_cost, currency = await self._cost(str(task.id))
        return DeliverableReceipt(
            receipt_id=str(uuid4()),
            task_id=str(task.id),
            project_id=task.project,
            execution_id=execution_id,
            deliverable_doc_slug=deliverable_doc_slug,
            issued_at=self._clock.now(),
            total_cost=total_cost,
            currency=currency,
            sources=await self._sources(execution_id),
            decisions=await self._decisions(task),
            tests=await self._tests(execution_id),
            red_team=await self._red_team(execution_id),
            cassette=self._cassette(),
        )

    async def _sources(
        self, execution_id: NotBlankStr
    ) -> tuple[ReceiptSourceEntry, ...]:
        """Collect distinct knowledge sources consulted during the run.

        Returns:
            One entry per distinct source id; resolved sources carry the
            registry title/uri/hash, unresolved ones a placeholder so
            validation can flag them.
        """
        rows = await self._knowledge_usage_records.query(
            KnowledgeUsageFilterSpec(execution_id=execution_id),
            limit=_SIGNAL_QUERY_LIMIT,
        )
        first_by_source: dict[str, str] = {}
        hashes: dict[str, str] = {}
        for row in rows:
            first_by_source.setdefault(row.source_id, row.chunk_id)
            hashes.setdefault(row.source_id, row.content_hash)
        entries: list[ReceiptSourceEntry] = []
        # Resolved sequentially on purpose: these are repository round-trips
        # on a single shared connection, so a TaskGroup fan-out yields no
        # speedup (aiosqlite serialises) and risks pool exhaustion on
        # Postgres. The distinct-source count is small in practice.
        for source_id, chunk_id in first_by_source.items():
            source = await self._knowledge_sources.get(source_id)
            title = _UNRESOLVED_TITLE if source is None else source.title
            uri = source_id if source is None else source.uri
            entries.append(
                ReceiptSourceEntry(
                    source_id=source_id,
                    chunk_id=chunk_id,
                    title=title,
                    uri=uri,
                    # The hash captured at retrieval time, NOT the live
                    # registry value: the validator compares this against
                    # the current source hash to detect content drift.
                    content_hash=hashes[source_id],
                )
            )
        return tuple(entries)

    async def _decisions(self, task: Task) -> tuple[ReceiptDecisionEntry, ...]:
        """Collect key decisions (with rationale) linked to the task.

        Returns:
            One entry per current DECISION brain entry linked to the
            task; empty when no brain is wired.
        """
        if self._brain_service is None:
            return ()
        summaries = await self._brain_service.list_current(
            project_id=task.project,
            entry_kind=BrainEntryKind.DECISION,
            related_task_id=str(task.id),
            limit=_SIGNAL_QUERY_LIMIT,
        )
        entries: list[ReceiptDecisionEntry] = []
        # Sequential for the same reason as ``_sources``: shared-connection
        # repository reads, small N. An entry listed by ``list_current`` can
        # be deleted before ``get_entry`` runs (TOCTOU); skip that entry
        # rather than letting a single missing decision sink the whole
        # best-effort receipt build.
        for summary in summaries:
            try:
                entry = await self._brain_service.get_entry(
                    project_id=task.project,
                    entry_id=summary.entry_id,
                )
            except BrainEntryNotFoundError:
                continue
            entries.append(
                ReceiptDecisionEntry(
                    entry_id=entry.entry_id,
                    revision=entry.revision,
                    title=entry.title,
                    rationale=entry.rationale,
                    recorded_at=entry.recorded_at,
                )
            )
        return tuple(entries)

    async def _cost(self, task_id: NotBlankStr) -> tuple[float, CurrencyCode]:
        """Aggregate cost and resolve the currency for the task.

        Returns:
            ``(total_cost, currency)``. Falls back to the dominant
            currency on a mixed-currency task and to the configured
            default currency when no cost records exist.
        """
        records = await self._cost_records.query(
            CostRecordFilterSpec(task_id=task_id),
            limit=_SIGNAL_QUERY_LIMIT,
        )
        if not records:
            return 0.0, self._default_currency
        try:
            total = await self._cost_records.aggregate(task_id=task_id)
        except MixedCurrencyAggregationError:
            return self._dominant_currency_total(records)
        return total, records[0].currency

    @staticmethod
    def _dominant_currency_total(
        records: tuple[CostRecord, ...],
    ) -> tuple[float, CurrencyCode]:
        """Sum the highest-total currency on a mixed-currency task.

        Returns:
            ``(total, currency)`` for the currency with the greatest
            summed cost; ties break on first appearance.
        """
        totals: dict[str, float] = {}
        for record in records:
            totals[record.currency] = totals.get(record.currency, 0.0) + record.cost
        dominant = max(totals, key=lambda c: totals[c])
        logger.warning(
            RECEIPT_MIXED_CURRENCY_COST,
            currency=dominant,
            currencies=sorted(totals),
        )
        return totals[dominant], dominant

    async def _tests(self, execution_id: NotBlankStr) -> tuple[ReceiptTestEntry, ...]:
        """Collect the test runs recorded during the run.

        Returns:
            One entry per persisted ``TESTS``-purpose code execution.
        """
        rows = await self._code_execution_records.query(
            CodeExecutionFilterSpec(
                execution_id=execution_id,
                purpose=CodeExecutionPurpose.TESTS,
            ),
            limit=_SIGNAL_QUERY_LIMIT,
        )
        return tuple(
            ReceiptTestEntry(
                record_id=row.record_id,
                command=row.command,
                returncode=row.returncode,
                passed=row.passed,
                timed_out=row.timed_out,
                executed_at=row.executed_at,
            )
            for row in rows
        )

    async def _red_team(self, execution_id: NotBlankStr) -> ReceiptRedTeamEntry | None:
        """Snapshot the red-team report for the run, when one exists.

        Returns:
            The red-team snapshot, or ``None`` when no repo is wired or
            no report exists for the run.
        """
        if self._redteam_reports is None:
            return None
        try:
            report = await self._redteam_reports.get(execution_id=execution_id)
        except RedTeamReportNotFoundError:
            logger.warning(
                RECEIPT_REDTEAM_UNAVAILABLE,
                execution_id=execution_id,
                reason="no_report_for_execution",
            )
            return None
        return self._red_team_entry(report)

    @staticmethod
    def _red_team_entry(report: RedTeamReport) -> ReceiptRedTeamEntry:
        """Build the red-team snapshot from a stored report.

        Returns:
            A :class:`ReceiptRedTeamEntry`. The verdict is derived as
            PASS (no findings) or PASS_WITH_FINDINGS: a BLOCK reroutes a
            task to rework, so a completed deliverable never carries one.
        """
        floor = severity_rank(BLOCKING_SEVERITY_FLOOR)
        high_plus = sum(
            1 for f in report.findings if severity_rank(f.severity) >= floor
        )
        verdict = (
            RedTeamVerdict.PASS
            if not report.findings
            else RedTeamVerdict.PASS_WITH_FINDINGS
        )
        return ReceiptRedTeamEntry(
            verdict=verdict,
            finding_count=len(report.findings),
            high_plus_count=high_plus,
            summary=report.summary,
            findings_snapshot=report.findings,
        )

    def _cassette(self) -> ReceiptCassetteRef | None:
        """Reference the active cassette, hashing its bytes, when present.

        Returns:
            A cassette reference with the file's content hash, or
            ``None`` when no cassette is configured or readable.
        """
        config = self._cassette_config
        if config is None or config.path is None:
            return None
        path = config.path
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning(
                RECEIPT_CASSETTE_UNAVAILABLE,
                path=str(path),
                reason="unreadable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        digest = hashlib.sha256(data).hexdigest()
        return ReceiptCassetteRef(path=str(path), content_hash=digest)
