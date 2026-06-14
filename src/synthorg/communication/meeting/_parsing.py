"""Shared helpers for parsing decisions and action items from LLM text.

Extracts structured decision strings and ``ActionItem`` instances from
free-form synthesis/summary responses produced by meeting protocol LLM
calls.
"""

import re
from typing import Final

from synthorg.communication.meeting.models import ActionItem
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import MEETING_PARSING_NO_SECTION

logger = get_logger(__name__)

# Hard cap on a section before list parsing. ``summary_text`` is derived
# from LLM output over agent-controlled (indirectly attacker-controllable)
# content; capping bounds both the line-classification work and any
# pathological single line, mirroring ``sanitize_message``'s pre-cap.
_MAX_SECTION_CHARS: Final[int] = 32_768

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

# A single list-item lead line (numbered or bulleted). Anchored,
# per-line, no nested repetition -- replaces the former multiline
# ``_LIST_ITEM_RE`` whose nested continuation group backtracked
# catastrophically on a long single line. The continuation join is now
# done by ``_extract_list_items`` line-by-line.
_LIST_LEAD_RE = re.compile(
    r"^[^\S\n]*(?:\d+[\.\)]|[-*\u2022])[^\S\n]*(.*)$",
)
# A line that terminates an item's continuation run: a markdown header
# or a ``key:`` line. Both anchored, no nested quantifiers.
_HEADER_LINE_RE = re.compile(r"^#+\s")
_KEY_LINE_RE = re.compile(r"^\S.*:\s*$")


def _extract_list_items(section: str) -> list[str]:
    """Extract bulleted / numbered list items with continuation joins.

    Linear single pass over the (capped) section: a bullet / numbered
    line starts an item; a following non-blank line that is neither a
    new bullet, a header, nor a ``key:`` line is folded in as a
    continuation. Eliminates the backtracking surface of the old
    nested-repetition regex.

    Returns:
        The list of joined item strings (whitespace-normalised).
    """
    items: list[str] = []
    current: list[str] | None = None

    def _flush() -> None:
        if current is not None:
            joined = " ".join(" ".join(current).split())
            if joined:
                items.append(joined)

    for line in section[:_MAX_SECTION_CHARS].split("\n"):
        lead = _LIST_LEAD_RE.match(line)
        if lead is not None:
            _flush()
            current = [lead.group(1)]
            continue
        if current is None:
            continue
        if not line.strip() or _HEADER_LINE_RE.match(line) or _KEY_LINE_RE.match(line):
            _flush()
            current = None
            continue
        current.append(line)
    _flush()
    return items


# Pattern for "assignee: <name>" or "(assigned to <name>)" at end of line
_ASSIGNEE_RE = re.compile(
    r"(?:"
    r"\(?assigned\s+to:?\s*(.+?)\)?"
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
            MEETING_PARSING_NO_SECTION,
            section="decisions",
        )
        return ()

    return tuple(_extract_list_items(section))


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
            MEETING_PARSING_NO_SECTION,
            section="action_items",
        )
        return ()

    items: list[ActionItem] = []
    for raw_text in _extract_list_items(section):
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
