"""Coordination error detectors.

Pure functions that analyse conversation histories to detect specific
categories of coordination errors.  Each detector returns a tuple of
``ErrorFinding`` instances (empty when no errors are found).

Detection heuristics are intentionally simple for the initial
implementation — full semantic analysis is planned for future iterations.
"""

import re
from collections import defaultdict
from typing import TYPE_CHECKING

from ai_company.budget.coordination_config import ErrorCategory
from ai_company.engine.classification.models import (
    ErrorFinding,
    ErrorSeverity,
)
from ai_company.observability import get_logger
from ai_company.observability.events.classification import (
    DETECTOR_COMPLETE,
    DETECTOR_START,
)
from ai_company.providers.enums import FinishReason, MessageRole

if TYPE_CHECKING:
    from ai_company.engine.loop_protocol import TurnRecord
    from ai_company.providers.models import ChatMessage

logger = get_logger(__name__)


# ── Constants ───────────────────────────────────────────────────

_MIN_TEXTS_FOR_CONTRADICTION = 2
_MIN_TEXTS_FOR_OMISSION = 4
_MIN_MENTIONS_FOR_DRIFT = 2
_HIGH_DRIFT_THRESHOLD = 50.0

# Words that are commonly capitalised but are not domain entities.
_COMMON_CAPITALISED_WORDS = frozenset(
    {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "Here",
        "There",
        "What",
        "When",
        "Where",
        "Which",
        "Who",
        "How",
        "Why",
        "Yes",
        "Not",
        "None",
        "True",
        "False",
        "All",
        "Some",
        "Each",
        "Every",
        "Any",
        "Other",
        "More",
        "Most",
        "Many",
    }
)


# ── Internal helpers ────────────────────────────────────────────


def _extract_assistant_texts(
    conversation: tuple[ChatMessage, ...],
) -> list[tuple[int, str]]:
    """Extract (index, text) pairs from assistant messages."""
    return [
        (i, msg.content)
        for i, msg in enumerate(conversation)
        if msg.role == MessageRole.ASSISTANT and msg.content
    ]


# Pattern: "X is true" / "X is not true"
_ASSERTION_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z][\w\s]{0,40}?)\s+"
    r"(?P<verb>is|are|was|were|should|must|will|can"
    r"|does|do|has|have)\s+"
    r"(?P<negation>not\s+)?"
    r"(?P<predicate>[A-Za-z][\w\s]{0,40})",
    re.IGNORECASE,
)


def _find_negation_pairs(
    texts: list[tuple[int, str]],
) -> list[tuple[int, int, str, str]]:
    """Find assertion pairs where one negates the other.

    Returns:
        List of (idx_a, idx_b, statement_a, statement_b) tuples.
    """
    assertions: list[tuple[int, str, str, bool]] = []

    for idx, text in texts:
        for match in _ASSERTION_PATTERN.finditer(text):
            subject = match.group("subject").strip().lower()
            verb = match.group("verb").lower()
            negation = match.group("negation") is not None
            predicate = match.group("predicate").strip().lower()
            key = f"{subject} {verb} {predicate}"
            assertions.append((idx, key, match.group(0).strip(), negation))

    pairs: list[tuple[int, int, str, str]] = []
    seen: dict[str, list[tuple[int, str, bool]]] = defaultdict(list)

    for idx, key, full_text, negated in assertions:
        for prev_idx, prev_text, prev_negated in seen[key]:
            if negated != prev_negated:
                pairs.append((prev_idx, idx, prev_text, full_text))
        seen[key].append((idx, full_text, negated))

    return pairs


_NUMBER_PATTERN = re.compile(
    r"(?P<context>[\w\s]{0,30}?)\s*"
    r"(?P<number>\d+(?:\.\d+)?)"
    r"(?P<unit>\s*%|\s*USD|\s*ms|\s*seconds?|\s*tokens?)?",
)


def _extract_numbers_with_context(
    text: str,
) -> list[tuple[str, float, str]]:
    """Extract (context_label, number, unit) triples from text."""
    results: list[tuple[str, float, str]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        context = match.group("context").strip().lower()
        if not context:
            continue
        try:
            number = float(match.group("number"))
        except ValueError:
            continue
        unit = (match.group("unit") or "").strip().lower()
        results.append((context, number, unit))
    return results


def _compute_drift(first_val: float, later_val: float) -> float | None:
    """Compute drift percentage, or ``None`` if not applicable.

    When ``first_val`` is zero and ``later_val`` is non-zero, returns
    a fixed 100.0% since percentage drift from a zero baseline is
    mathematically undefined.  When both are zero, returns ``None``
    (no drift).
    """
    if first_val == 0.0:
        return 100.0 if later_val != 0.0 else None
    return abs(later_val - first_val) / abs(first_val) * 100


def _check_drift_in_group(
    context: str,
    unit: str,
    mentions: list[tuple[int, float]],
    threshold_percent: float,
) -> tuple[ErrorFinding, ...]:
    """Check a single number group for drift and return findings."""
    first_idx, first_val = mentions[0]
    findings: list[ErrorFinding] = []
    for later_idx, later_val in mentions[1:]:
        drift_pct = _compute_drift(first_val, later_val)
        if drift_pct is None:
            continue
        if drift_pct > threshold_percent:
            label = f"{context} {unit}".strip()
            severity = (
                ErrorSeverity.HIGH
                if drift_pct > _HIGH_DRIFT_THRESHOLD
                else ErrorSeverity.MEDIUM
            )
            findings.append(
                ErrorFinding(
                    category=ErrorCategory.NUMERICAL_DRIFT,
                    severity=severity,
                    description=(
                        f"Numerical drift of {drift_pct:.1f}% detected for '{label}'"
                    ),
                    evidence=(
                        f"Message {first_idx}: {first_val}",
                        f"Message {later_idx}: {later_val}",
                    ),
                    turn_range=(first_idx, later_idx),
                )
            )
    return tuple(findings)


_ENTITY_PATTERN = re.compile(r"\b([A-Z][a-zA-Z]{2,})\b")


def _extract_entities(text: str) -> set[str]:
    """Extract capitalised entity names from text.

    Matches CamelCase identifiers and capitalised words (3+ chars)
    that likely represent domain entities, class names, or services.
    """
    return set(_ENTITY_PATTERN.findall(text))


# ── Public detectors ────────────────────────────────────────────


def detect_logical_contradictions(
    conversation: tuple[ChatMessage, ...],
) -> tuple[ErrorFinding, ...]:
    """Detect logical contradictions across assistant messages.

    Scans for assertion-like statements where one message affirms
    something and another negates it (e.g. "X is true" vs "X is
    not true").

    Args:
        conversation: Full conversation history.

    Returns:
        Tuple of findings for each contradiction pair detected.
    """
    logger.debug(
        DETECTOR_START,
        detector="logical_contradictions",
        message_count=len(conversation),
    )
    texts = _extract_assistant_texts(conversation)
    if len(texts) < _MIN_TEXTS_FOR_CONTRADICTION:
        logger.debug(
            DETECTOR_COMPLETE,
            detector="logical_contradictions",
            finding_count=0,
        )
        return ()

    pairs = _find_negation_pairs(texts)
    findings = tuple(
        ErrorFinding(
            category=ErrorCategory.LOGICAL_CONTRADICTION,
            severity=ErrorSeverity.HIGH,
            description="Contradictory assertions detected across assistant messages",
            evidence=(
                f"Message {idx_a}: {stmt_a!r}",
                f"Message {idx_b}: {stmt_b!r}",
            ),
            turn_range=(idx_a, idx_b),
        )
        for idx_a, idx_b, stmt_a, stmt_b in pairs
    )
    logger.debug(
        DETECTOR_COMPLETE,
        detector="logical_contradictions",
        finding_count=len(findings),
    )
    return findings


def detect_numerical_drift(
    conversation: tuple[ChatMessage, ...],
    *,
    threshold_percent: float = 5.0,
) -> tuple[ErrorFinding, ...]:
    """Detect numerical value drift across assistant messages.

    Extracts numbers with surrounding context labels, groups them
    by label+unit, and flags when the same quantity drifts more
    than ``threshold_percent`` between mentions.

    Args:
        conversation: Full conversation history.
        threshold_percent: Maximum allowed drift percentage.

    Returns:
        Tuple of findings for each drifted quantity.
    """
    if threshold_percent <= 0:
        msg = "threshold_percent must be positive"
        raise ValueError(msg)
    logger.debug(
        DETECTOR_START,
        detector="numerical_drift",
        message_count=len(conversation),
    )
    texts = _extract_assistant_texts(conversation)
    if not texts:
        logger.debug(
            DETECTOR_COMPLETE,
            detector="numerical_drift",
            finding_count=0,
        )
        return ()

    # Group: (context_label, unit) -> [(msg_index, value)]
    groups: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for idx, text in texts:
        for ctx, number, unit in _extract_numbers_with_context(text):
            groups[(ctx, unit)].append((idx, number))

    all_findings: list[ErrorFinding] = []
    for (ctx, unit), mentions in groups.items():
        if len(mentions) < _MIN_MENTIONS_FOR_DRIFT:
            continue
        all_findings.extend(
            _check_drift_in_group(ctx, unit, mentions, threshold_percent),
        )

    result = tuple(all_findings)
    logger.debug(
        DETECTOR_COMPLETE,
        detector="numerical_drift",
        finding_count=len(result),
    )
    return result


def detect_context_omissions(
    conversation: tuple[ChatMessage, ...],
) -> tuple[ErrorFinding, ...]:
    """Detect entities that are introduced early but vanish later.

    Tracks capitalised entity names across assistant messages.  If
    an entity appears in the first half but is absent from the
    second half, it is flagged as a potential context omission.

    Args:
        conversation: Full conversation history.

    Returns:
        Tuple of findings for each omitted entity.
    """
    logger.debug(
        DETECTOR_START,
        detector="context_omissions",
        message_count=len(conversation),
    )
    texts = _extract_assistant_texts(conversation)
    if len(texts) < _MIN_TEXTS_FOR_OMISSION:
        logger.debug(
            DETECTOR_COMPLETE,
            detector="context_omissions",
            finding_count=0,
        )
        return ()

    midpoint = len(texts) // 2
    first_half = texts[:midpoint]
    second_half = texts[midpoint:]

    first_entities: set[str] = set()
    for _idx, text in first_half:
        first_entities |= _extract_entities(text)

    second_entities: set[str] = set()
    for _idx, text in second_half:
        second_entities |= _extract_entities(text)

    omitted = first_entities - second_entities - _COMMON_CAPITALISED_WORDS
    if not omitted:
        logger.debug(
            DETECTOR_COMPLETE,
            detector="context_omissions",
            finding_count=0,
        )
        return ()

    first_end_idx = first_half[-1][0]
    second_start_idx = second_half[0][0]

    findings = tuple(
        ErrorFinding(
            category=ErrorCategory.CONTEXT_OMISSION,
            severity=ErrorSeverity.MEDIUM,
            description=(
                f"Entity '{entity}' introduced in early "
                f"messages but absent from later conversation"
            ),
            evidence=(
                f"Present in messages 0-{first_end_idx}",
                f"Absent from messages {second_start_idx}+",
            ),
            turn_range=(0, second_start_idx),
        )
        for entity in sorted(omitted)
    )
    logger.debug(
        DETECTOR_COMPLETE,
        detector="context_omissions",
        finding_count=len(findings),
    )
    return findings


def detect_coordination_failures(
    conversation: tuple[ChatMessage, ...],
    turns: tuple[TurnRecord, ...],
) -> tuple[ErrorFinding, ...]:
    """Detect coordination failures from tool errors and signals.

    Checks for:
    - Tool execution errors (``ToolResult.is_error=True``).
    - Error-indicating finish reasons in turn records.

    Args:
        conversation: Full conversation history.
        turns: Per-turn metadata from execution.

    Returns:
        Tuple of findings for each coordination failure signal.
    """
    logger.debug(
        DETECTOR_START,
        detector="coordination_failures",
        message_count=len(conversation),
        turn_count=len(turns),
    )
    findings: list[ErrorFinding] = []

    for i, msg in enumerate(conversation):
        if (
            msg.role == MessageRole.TOOL
            and msg.tool_result is not None
            and msg.tool_result.is_error
        ):
            findings.append(
                ErrorFinding(
                    category=ErrorCategory.COORDINATION_FAILURE,
                    severity=ErrorSeverity.HIGH,
                    description="Tool execution error detected",
                    evidence=(
                        f"Message {i}: tool_call_id="
                        f"'{msg.tool_result.tool_call_id}' "
                        f"returned error",
                    ),
                    turn_range=(i, i),
                )
            )

    findings.extend(
        ErrorFinding(
            category=ErrorCategory.COORDINATION_FAILURE,
            severity=ErrorSeverity.HIGH,
            description="Error finish reason in turn record",
            evidence=(
                f"Turn {turn.turn_number}: finish_reason={turn.finish_reason.value}",
            ),
            turn_range=(turn_idx, turn_idx),
        )
        for turn_idx, turn in enumerate(turns)
        if turn.finish_reason == FinishReason.ERROR
    )

    result = tuple(findings)
    logger.debug(
        DETECTOR_COMPLETE,
        detector="coordination_failures",
        finding_count=len(result),
    )
    return result
