# module-kind: service
"""Validate a deliverable receipt for consistency.

Three structural checks, none of which re-runs the agent:

1. **Sources resolve** -- every listed source still exists in the
   knowledge registry with a matching content hash.
2. **Cassette loads** -- when a cassette is referenced, its file reads,
   hashes to the recorded digest, and parses as a cassette document.
3. **Tests reconcile** -- every claimed test result matches a persisted
   code-execution record for the run (same return code).

Red-team is best-effort: when the (process-local) report is still
available it is reconciled by finding count; otherwise the receipt's
own snapshot stands (its internal counts are model-enforced).
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from synthorg.deliverable_receipts.models import (
    DeliverableReceipt,
    ReceiptValidationResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    RECEIPT_CASSETTE_UNAVAILABLE,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionRecordRepository,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from synthorg.providers.cassette.store import CassetteDocument
from synthorg.security.redteam.errors import RedTeamReportNotFoundError
from synthorg.security.redteam.protocol import RedTeamReportRepository

logger = get_logger(__name__)

_SIGNAL_QUERY_LIMIT: Final[int] = 1000


class ReceiptValidator:
    """Check a :class:`DeliverableReceipt` for signal consistency."""

    def __init__(
        self,
        *,
        knowledge_sources: KnowledgeSourceRepository,
        code_execution_records: CodeExecutionRecordRepository,
        redteam_reports: RedTeamReportRepository | None = None,
    ) -> None:
        self._knowledge_sources = knowledge_sources
        self._code_execution_records = code_execution_records
        self._redteam_reports = redteam_reports

    async def validate(self, receipt: DeliverableReceipt) -> ReceiptValidationResult:
        """Validate every present signal on ``receipt``.

        Returns:
            A :class:`ReceiptValidationResult`; ``valid`` is ``True`` only
            when no inconsistency was found.
        """
        errors: list[str] = []
        errors.extend(await self._check_sources(receipt))
        errors.extend(await self._check_cassette(receipt))
        errors.extend(await self._check_tests(receipt))
        errors.extend(await self._check_red_team(receipt))
        return ReceiptValidationResult(valid=not errors, errors=tuple(errors))

    async def _check_sources(self, receipt: DeliverableReceipt) -> list[str]:
        """Confirm each source resolves with a matching content hash.

        Returns:
            One error string per source that fails to resolve or whose
            content hash drifted; empty when all resolve.
        """
        errors: list[str] = []
        for entry in receipt.sources:
            source = await self._knowledge_sources.get(entry.source_id)
            if source is None:
                errors.append(f"source {entry.source_id!r} does not resolve")
            elif source.content_hash != entry.content_hash:
                errors.append(
                    f"source {entry.source_id!r} content hash drifted "
                    f"(receipt {entry.content_hash!r} != source "
                    f"{source.content_hash!r})"
                )
        return errors

    async def _check_cassette(self, receipt: DeliverableReceipt) -> list[str]:
        """Confirm the cassette loads, hashes, and parses (when present).

        Async: the file read is offloaded via ``asyncio.to_thread`` so a
        large cassette does not block the event loop during validation.

        Returns:
            Error strings for an unreadable file, a drifted hash, or an
            unparseable cassette; empty when absent or consistent.
        """
        if receipt.cassette is None:
            return []
        path = Path(receipt.cassette.path)
        try:
            # Offload the synchronous file read so a large cassette does not
            # block the event loop during validation.
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            # The cassette path is an internal filesystem location: log it
            # server-side but return an opaque reason so the REST validation
            # response (visible to any read-access caller) does not disclose
            # host directory layout.
            logger.warning(
                RECEIPT_CASSETTE_UNAVAILABLE,
                path=str(path),
                reason="unreadable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ["referenced cassette could not be read"]
        digest = hashlib.sha256(data).hexdigest()
        if digest != receipt.cassette.content_hash:
            return ["cassette content hash drifted from the recorded digest"]
        try:
            CassetteDocument.model_validate_json(data.decode())
        except (ValidationError, UnicodeDecodeError) as exc:
            logger.warning(
                RECEIPT_CASSETTE_UNAVAILABLE,
                path=str(path),
                reason="unparseable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ["referenced cassette is not a valid cassette document"]
        return []

    async def _check_tests(self, receipt: DeliverableReceipt) -> list[str]:
        """Reconcile claimed test results against persisted records.

        Returns:
            One error string per claimed test with no matching record or
            a mismatched return code; empty when all reconcile.
        """
        if not receipt.tests:
            return []
        rows = await self._code_execution_records.query(
            CodeExecutionFilterSpec(execution_id=receipt.execution_id),
            limit=_SIGNAL_QUERY_LIMIT,
        )
        by_id = {row.record_id: row for row in rows}
        errors: list[str] = []
        for entry in receipt.tests:
            row = by_id.get(entry.record_id)
            if row is None:
                errors.append(
                    f"test record {entry.record_id!r} has no persisted record"
                )
            elif row.returncode != entry.returncode:
                errors.append(
                    f"test record {entry.record_id!r} return code mismatch "
                    f"(receipt {entry.returncode} != record {row.returncode})"
                )
        return errors

    async def _check_red_team(self, receipt: DeliverableReceipt) -> list[str]:
        """Reconcile the red-team snapshot against the live report if present.

        Returns:
            A single error string when the live report's finding count
            disagrees with the snapshot; empty otherwise.
        """
        if receipt.red_team is None or self._redteam_reports is None:
            return []
        try:
            report = await self._redteam_reports.get(execution_id=receipt.execution_id)
        except RedTeamReportNotFoundError:
            # Process-local report already gone; the snapshot stands.
            return []
        if len(report.findings) != receipt.red_team.finding_count:
            return [
                "red-team finding count mismatch "
                f"(receipt {receipt.red_team.finding_count} != "
                f"report {len(report.findings)})"
            ]
        return []
