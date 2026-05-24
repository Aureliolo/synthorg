"""Composite detector that merges findings from multiple variants.

Runs multiple detectors of the same category concurrently via
``asyncio.TaskGroup`` and deduplicates the collected findings.
Sub-detector failures are isolated on a per-task basis -- a single
detector raising an exception never cancels its siblings or
discards their findings.  Sub-detectors are also filtered by
``context.scope`` so they are only invoked on contexts they
declared they support.
"""

import asyncio
import hashlib
from typing import TYPE_CHECKING

from synthorg.engine.classification.models import (
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.classification import (
    CLASSIFICATION_FINDING_DEDUPLICATED,
    DETECTOR_COMPLETE,
    DETECTOR_ERROR,
    DETECTOR_SCOPE_FILTERED,
    DETECTOR_START,
)

if TYPE_CHECKING:
    from synthorg.budget.coordination_config import (
        DetectionScope,
        ErrorCategory,
    )
    from synthorg.engine.classification.protocol import (
        DetectionContext,
        Detector,
    )

logger = get_logger(__name__)

_SEVERITY_ORDER = {
    ErrorSeverity.LOW: 0,
    ErrorSeverity.MEDIUM: 1,
    ErrorSeverity.HIGH: 2,
}


def _dedup_key(finding: ErrorFinding) -> str:
    """Build a dedup key from turn_range + full description hash + category.

    Uses the full SHA-256 hex digest (not a truncated prefix) so the
    key actually matches the documented
    ``(turn_range, sha256(description), category)`` identity and
    cannot produce false merges via short-digest collisions.
    """
    desc_hash = hashlib.sha256(
        finding.description.encode(),
    ).hexdigest()
    return f"{finding.turn_range!r}|{desc_hash}|{finding.category.value}"


def deduplicate_findings(
    findings: tuple[ErrorFinding, ...],
) -> tuple[ErrorFinding, ...]:
    """Remove duplicate findings, keeping highest severity per group.

    Dedup key: ``(turn_range, sha256(description), category)``.
    When severity ties, evidence tuples are merged with set-based
    de-duplication so identical evidence strings contributed by
    different sub-detectors do not accumulate.

    Args:
        findings: Raw findings from multiple detectors.

    Returns:
        Deduplicated findings tuple.
    """
    if not findings:
        return ()

    groups: dict[str, ErrorFinding] = {}
    for finding in findings:
        key = _dedup_key(finding)
        existing = groups.get(key)
        if existing is None:
            groups[key] = finding
            continue
        # Keep the higher severity; merge evidence set-wise.
        existing_rank = _SEVERITY_ORDER[existing.severity]
        new_rank = _SEVERITY_ORDER[finding.severity]
        winner = finding if new_rank > existing_rank else existing
        seen: set[str] = set()
        merged: list[str] = []
        for evidence in (*existing.evidence, *finding.evidence):
            if evidence not in seen:
                seen.add(evidence)
                merged.append(evidence)
        groups[key] = ErrorFinding(
            category=winner.category,
            severity=winner.severity,
            description=winner.description,
            evidence=tuple(merged),
            turn_range=winner.turn_range,
        )

    deduped = tuple(groups.values())
    removed = len(findings) - len(deduped)
    if removed > 0:
        logger.debug(
            CLASSIFICATION_FINDING_DEDUPLICATED,
            removed_count=removed,
            original_count=len(findings),
        )
    return deduped


async def _run_detector_safely(
    detector: Detector,
    context: DetectionContext,
) -> tuple[ErrorFinding, ...]:
    """Invoke a sub-detector and trap all non-critical exceptions.

    Catches every ``Exception`` subclass the sub-detector may raise,
    logs it at exception level with the detector name, and returns
    an empty findings tuple.  ``MemoryError`` and ``RecursionError``
    are re-raised so they surface to the pipeline's outer handler
    (see CLAUDE.md §Resilience).

    Args:
        detector: Sub-detector instance to invoke.
        context: Detection context passed through unchanged.

    Returns:
        The detector's findings on success, or ``()`` on failure.
    """
    try:
        return await detector.detect(context)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_exception_redacted(
            logger,
            DETECTOR_ERROR,
            exc,
            detector=type(detector).__name__,
            agent_id=context.agent_id,
            task_id=context.task_id,
            message_count=len(context.execution_result.context.conversation),
        )
        return ()


class CompositeDetector:
    """Merges findings from multiple variants of the same category.

    All sub-detectors must target the same ``ErrorCategory``.  The
    composite filters sub-detectors by ``context.scope`` (only
    detectors whose ``supported_scopes`` contains the active scope
    are invoked), runs the survivors concurrently, and isolates
    each sub-detector failure so a single raise never cancels its
    siblings or discards their findings.

    Args:
        detectors: Detector instances to compose.

    Raises:
        ValueError: If detectors have mixed categories or
            the tuple is empty.
    """

    def __init__(self, detectors: tuple[Detector, ...]) -> None:
        if not detectors:
            msg = "detectors must not be empty"
            raise ValueError(msg)
        cats = {d.category for d in detectors}
        if len(cats) > 1:
            msg = f"All detectors must share a category, got: {cats}"
            raise ValueError(msg)
        self._detectors = detectors

    @property
    def category(self) -> ErrorCategory:
        """Shared error category of all sub-detectors."""
        return self._detectors[0].category

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Union of all sub-detector scopes."""
        scopes: set[DetectionScope] = set()
        for d in self._detectors:
            scopes |= d.supported_scopes
        return frozenset(scopes)

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run eligible sub-detectors and return deduplicated findings.

        Sub-detectors whose ``supported_scopes`` does not contain
        ``context.scope`` are filtered out before scheduling and
        their exclusion is logged at DEBUG level.  Survivors run
        concurrently inside an ``asyncio.TaskGroup``; each call is
        wrapped by :func:`_run_detector_safely` so a single failing
        variant cannot cancel its siblings or discard their
        findings.

        Args:
            context: Detection context with execution data.

        Returns:
            Deduplicated findings from all eligible sub-detectors.
        """
        eligible = tuple(
            d for d in self._detectors if context.scope in d.supported_scopes
        )
        filtered = tuple(
            d for d in self._detectors if context.scope not in d.supported_scopes
        )
        for detector in filtered:
            logger.debug(
                DETECTOR_SCOPE_FILTERED,
                detector=type(detector).__name__,
                category=self.category.value,
                context_scope=context.scope.value,
                supported_scopes=sorted(s.value for s in detector.supported_scopes),
            )
        if not eligible:
            logger.debug(
                DETECTOR_COMPLETE,
                detector=f"composite({self.category.value})",
                finding_count=0,
                reason="no sub-detectors support the context scope",
            )
            return ()

        logger.debug(
            DETECTOR_START,
            detector=f"composite({self.category.value})",
            message_count=len(
                context.execution_result.context.conversation,
            ),
            sub_detector_count=len(eligible),
        )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_run_detector_safely(detector, context))
                for detector in eligible
            ]

        all_findings: list[ErrorFinding] = []
        for task in tasks:
            all_findings.extend(task.result())

        deduped = deduplicate_findings(tuple(all_findings))
        logger.debug(
            DETECTOR_COMPLETE,
            detector=f"composite({self.category.value})",
            finding_count=len(deduped),
        )
        return deduped
