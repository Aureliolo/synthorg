# module-kind: code
"""JSON-response parsing for the LLM-backed semantic detectors.

Pure, side-effect-free helpers split out of :mod:`semantic_detectors` so
the detector module stays focused on provider orchestration and budget
tracking. Malformed JSON or invalid items are logged at DEBUG and
skipped: a bad response degrades to an empty finding set rather than
failing the detector.
"""

import json
from types import MappingProxyType
from typing import Final

from synthorg.budget.coordination_config import ErrorCategory
from synthorg.engine.classification.models import (
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.classification import DETECTOR_PARSE_ERROR

logger = get_logger(__name__)

_SEVERITY_MAP: Final[MappingProxyType[str, ErrorSeverity]] = MappingProxyType(
    {
        "low": ErrorSeverity.LOW,
        "medium": ErrorSeverity.MEDIUM,
        "high": ErrorSeverity.HIGH,
    },
)


def parse_findings(
    raw: str | None,
    category: ErrorCategory,
) -> tuple[ErrorFinding, ...]:
    """Parse LLM JSON output into ErrorFinding tuples.

    Expected format::

        [
            {
                "description": "...",
                "severity": "high|medium|low",
                "evidence": ["..."],
                "turn_start": 0,
                "turn_end": 2,
            }
        ]

    Malformed JSON or invalid items are logged at DEBUG level and
    skipped -- they do not cause the detector to fail.

    Returns:
        Tuple of well-formed :class:`ErrorFinding` records; ``()`` on
        empty input, invalid JSON, or non-list output.
    """
    if not raw:
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug(
            DETECTOR_PARSE_ERROR,
            category=category.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            raw_length=len(raw),
        )
        return ()
    if not isinstance(items, list):
        logger.debug(
            DETECTOR_PARSE_ERROR,
            category=category.value,
            reason="response is not a JSON array",
            actual_type=type(items).__name__,
        )
        return ()

    findings: list[ErrorFinding] = []
    for idx, item in enumerate(items):
        finding = _parse_single_finding(item, idx, category)
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def _parse_single_finding(
    item: object,
    idx: int,
    category: ErrorCategory,
) -> ErrorFinding | None:
    """Parse a single item from an LLM JSON array.

    Returns ``None`` when the item is malformed -- parse errors
    are logged at DEBUG level for operator visibility.

    Returns:
        A well-formed :class:`ErrorFinding`; ``None`` when the JSON
        object is missing required fields or has the wrong shape.
    """
    if not isinstance(item, dict):
        logger.debug(
            DETECTOR_PARSE_ERROR,
            category=category.value,
            item_index=idx,
            reason="item is not a JSON object",
        )
        return None
    desc = item.get("description", "")
    if not isinstance(desc, str) or not desc.strip():
        logger.debug(
            DETECTOR_PARSE_ERROR,
            category=category.value,
            item_index=idx,
            reason="missing or empty description",
        )
        return None
    desc = desc.strip()
    severity = _SEVERITY_MAP.get(
        str(item.get("severity", "medium")).lower(),
        ErrorSeverity.MEDIUM,
    )
    evidence_raw = item.get("evidence", [])
    if not isinstance(evidence_raw, list):
        logger.debug(
            DETECTOR_PARSE_ERROR,
            category=category.value,
            item_index=idx,
            reason="evidence is not a list, coercing to empty",
        )
        evidence_raw = []
    evidence = tuple(e for e in evidence_raw if isinstance(e, str) and e.strip())
    turn_range: tuple[int, int] | None = None
    turn_start = item.get("turn_start")
    turn_end = item.get("turn_end")
    if (
        isinstance(turn_start, int)
        and not isinstance(turn_start, bool)
        and isinstance(turn_end, int)
        and not isinstance(turn_end, bool)
        and turn_start >= 0
        and turn_end >= turn_start
    ):
        turn_range = (turn_start, turn_end)

    return ErrorFinding(
        category=category,
        severity=severity,
        description=desc,
        evidence=evidence,
        turn_range=turn_range,
    )


__all__ = ["parse_findings"]
