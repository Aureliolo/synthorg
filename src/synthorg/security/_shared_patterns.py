"""Shared regex patterns for the security package.

Single source of truth for the control / formatting character class used
by both stage-1 description stripping
(:mod:`synthorg.security.information_stripper`) and stage-2 LLM-response
sanitization (:mod:`synthorg.security.safety_classifier`). Keeping the
codepoint ranges in one place prevents the two security boundaries from
drifting apart when the invisible-character set is updated.
"""

import re
from typing import Final


def _build_control_char_re() -> re.Pattern[str]:
    """Build the control / formatting character-stripping pattern.

    Best-effort coverage: ASCII control (C0/DEL), Unicode bidi overrides,
    zero-width chars, line/paragraph separators, and known invisible
    characters used in prompt injection payloads. Built from codepoint
    ranges so the source carries no literal invisible characters.

    Returns:
        The compiled control-character class pattern.
    """
    ranges: tuple[tuple[int, int], ...] = (
        (0x00, 0x1F),  # ASCII control (C0)
        (0x7F, 0x7F),  # DEL
        (0x200B, 0x200F),  # zero-width and bidi marks
        (0x2028, 0x2029),  # line / paragraph separators
        (0x202A, 0x202E),  # bidi embedding/override
        (0x2066, 0x2069),  # bidi isolate
        (0x2800, 0x2800),  # braille blank (invisible)
        (0x3164, 0x3164),  # hangul filler (invisible)
        (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
    )
    parts = [chr(lo) if lo == hi else f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges]
    return re.compile("[" + "".join(parts) + "]")


# Strips control and formatting characters that could hide prompt
# injection payloads. Shared by stage-1 stripping and stage-2 LLM-reason
# sanitization so the codepoint set is maintained in one place.
CONTROL_CHAR_RE: Final[re.Pattern[str]] = _build_control_char_re()
