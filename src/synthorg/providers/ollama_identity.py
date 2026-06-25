# module-kind: code
"""Parse a coarse family + generation from an Ollama model id.

Pure, no I/O -- kept apart from the probing network code so the matcher can
group versions of one model line (and pin the newest) from the id alone.
"""

import re
from typing import Final

_OLLAMA_FAMILY_RE: Final = re.compile(r"^[a-z]+")
_OLLAMA_GENERATION_RE: Final = re.compile(r"\d+(?:\.\d+)?")
_MIN_FAMILY_LEN: Final[int] = 2


def parse_ollama_identity(model_id: str) -> tuple[str | None, float | None]:
    """Extract a coarse family + generation from an Ollama model id.

    Ollama ids embed the model line and version in the name (``deepseek-v4-pro``,
    ``glm-5.2``, ``gemma4:26b``). The family is the leading alphabetic run and
    the generation is the first version number, so the matcher can group
    versions of one model line, pin the newest, and spread across distinct
    families. Heuristic, not authoritative: an unrecognised shape yields
    ``(None, None)`` rather than a wrong guess.

    Args:
        model_id: The model id from the listing (tag included).

    Returns:
        ``(family, generation)``; either element is ``None`` when not derivable.
    """
    base = model_id.split(":", 1)[0].lower()
    fam_match = _OLLAMA_FAMILY_RE.match(base)
    family = (
        fam_match.group(0)
        if fam_match and len(fam_match.group(0)) >= _MIN_FAMILY_LEN
        else None
    )
    gen_match = _OLLAMA_GENERATION_RE.search(base)
    generation = float(gen_match.group(0)) if gen_match else None
    return family, generation
