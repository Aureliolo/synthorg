"""Shared helpers for parsing decisions and action items from LLM text.

Extracts structured decision strings and ``ActionItem`` instances from
free-form synthesis/summary responses produced by meeting protocol LLM
calls.
"""

import re

from ai_company.communication.meeting.models import ActionItem
from ai_company.observability import get_logger

logger = get_logger(__name__)

# Patterns for section headers
_DECISIONS_HEADER_RE = re.compile(
    r"^#+\s*decisions?\b|^decisions?\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_ACTION_ITEMS_HEADER_RE = re.compile(
    r"^#+\s*action\s+items?\b|^action\s+items?\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_ANY_HEADER_RE = re.compile(
    r"^#+\s+\S|^(?!\s*(?:\d+[\.\)]\s|-\s|\*\s|\u2022\s))\S.*:\s*$",
    re.MULTILINE,
)

# List item patterns (numbered or bulleted), capturing continuation lines
_LIST_ITEM_RE = re.compile(
    r"^[^\S\n]*(?:\d+[\.\)][^\S\n]*|-[^\S\n]*|\*[^\S\n]*|\u2022[^\S\n]*)"
    r"(.+(?:\n(?![^\S\n]*(?:\d+[\.\)]\s|-\s|\*\s|\u2022\s)|#+\s|\S.*:\s*$).+)*)",
    re.MULTILINE,
)

# Pattern for "assignee: <name>" or "(assigned to <name>)" at end of line
_ASSIGNEE_RE = re.compile(
    r"(?:"
    r"\(?assigned?\s+to:?\s*(.+?)\)?"
    r"|assignee:?\s*(.+?)"
    r")\s*$",
    re.IGNORECASE,
)


def _extract_section(
    text: str,
    header_re: re.Pattern[str],
) -> str:
    """Extract text between a section header and the next header.

    Args:
        text: Full response text.
        header_re: Compiled regex matching the section header.

    Returns:
        Section body text, or empty string if header not found.
    """
    match = header_re.search(text)
    if match is None:
        return ""

    start = match.end()
    # Find the next header after this section
    next_header = _ANY_HEADER_RE.search(text, start)
    end = next_header.start() if next_header is not None else len(text)

    return text[start:end]


def parse_decisions(summary_text: str) -> tuple[str, ...]:
    """Parse decisions from an LLM summary/synthesis response.

    Looks for a "Decisions" section header, then extracts numbered
    or bulleted list items.  Falls back to empty tuple if no
    decisions section is found.

    Args:
        summary_text: The full summary/synthesis text from the LLM.

    Returns:
        Tuple of decision strings (may be empty).
    """
    section = _extract_section(summary_text, _DECISIONS_HEADER_RE)
    if not section:
        logger.debug(
            "meeting.parsing.no_section",
            section="decisions",
        )
        return ()

    decisions: list[str] = []
    for match in _LIST_ITEM_RE.finditer(section):
        # Join continuation lines into a single string
        text = " ".join(match.group(1).split())
        if text:
            decisions.append(text)

    return tuple(decisions)


def _parse_assignee(text: str) -> tuple[str, str | None]:
    """Extract assignee from an action item line.

    Args:
        text: The action item text (may contain assignee info).

    Returns:
        Tuple of (cleaned description, assignee_id or None).
    """
    match = _ASSIGNEE_RE.search(text)
    if match is None:
        return text, None

    assignee = (match.group(1) or match.group(2) or "").strip()
    # Remove the assignee part from the description
    description = text[: match.start()].strip()
    # Strip trailing punctuation left over
    description = description.rstrip(" -,;:")

    if not assignee or not description:
        return text, None

    return description, assignee


def parse_action_items(
    summary_text: str,
) -> tuple[ActionItem, ...]:
    """Parse action items from an LLM summary/synthesis response.

    Looks for an "Action Items" section header, then extracts
    bulleted or numbered list items. Attempts to detect assignee
    information within each item.

    Args:
        summary_text: The full summary/synthesis text from the LLM.

    Returns:
        Tuple of ActionItem instances (may be empty).
    """
    section = _extract_section(summary_text, _ACTION_ITEMS_HEADER_RE)
    if not section:
        logger.debug(
            "meeting.parsing.no_section",
            section="action_items",
        )
        return ()

    items: list[ActionItem] = []
    for match in _LIST_ITEM_RE.finditer(section):
        # Join continuation lines into a single string
        raw_text = " ".join(match.group(1).split())
        if not raw_text:
            continue

        description, assignee_id = _parse_assignee(raw_text)
        if not description:
            continue

        items.append(
            ActionItem(
                description=description,
                assignee_id=assignee_id,
            )
        )

    return tuple(items)
