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
_OLLAMA_SPECIALISATION_RE: Final = re.compile(r"-(coder|code|embedding|embed)\b")
"""A specialisation suffix that makes a model a distinct lineage.

An Ollama line ships a base chat model alongside specialised siblings
(``qwen3-coder``, ``qwen3-embedding``, ``kimi-k2.7-code``) that are NOT
drop-in replacements for the base line. Folding the suffix into the family
keeps each specialisation its own lineage so the upgrade recommender never
offers a general (or embedding) model as the newer pick for a coder pin.
Tier suffixes (``-pro`` / ``-flash``) are deliberately excluded: they are
the same lineage at a different size/latency tier and the recommender's
scorer ranks within the family. Kept aligned with the ``ollama`` rules in
:data:`synthorg.providers.presets.MODEL_FAMILY_RULES`, which fold the same
suffixes on the LiteLLM-enrichment discovery path.
"""


def parse_ollama_identity(model_id: str) -> tuple[str | None, float | None]:
    """Extract a family + generation from an Ollama model id.

    Ollama ids embed the model line and version in the name (``deepseek-v4-pro``,
    ``glm-5.2``, ``gemma4:26b``). The family is the leading alphabetic run
    (with any ``-coder`` / ``-code`` / ``-embedding`` specialisation suffix
    folded in so a specialised sibling is its own lineage) and the generation
    is the first version number, so the matcher can group versions of one
    model line, pin the newest, and spread across distinct families.
    Heuristic, not authoritative: an unrecognised shape yields ``(None, None)``
    rather than a wrong guess.

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
    if family is not None:
        specialisation = _OLLAMA_SPECIALISATION_RE.search(base)
        if specialisation is not None:
            family = f"{family}-{specialisation.group(1)}"
    gen_match = _OLLAMA_GENERATION_RE.search(base)
    generation = float(gen_match.group(0)) if gen_match else None
    return family, generation
