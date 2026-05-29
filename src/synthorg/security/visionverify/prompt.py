"""Prompt construction for the ``llm_vision`` verifier.

The brief and acceptance criteria are attacker-controllable (they
originate from a client request), so they are fenced with
``wrap_untrusted`` and the system prompt appends the
``untrusted_content_directive`` so the model treats fenced content as
data, not instructions. Screenshots travel as structured ``image_parts``
on the user message, never as text.
"""

import json
from typing import TYPE_CHECKING, Final

from synthorg.engine.prompt_safety import (
    TAG_CRITERIA_JSON,
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)

if TYPE_CHECKING:
    from synthorg.security.visionverify.models import VisionReviewInput

_SYSTEM_PROMPT: Final[str] = (
    "You are a meticulous UI verification evaluator. You are shown "
    "screenshots of a running application and the brief it was built "
    "against. Decide whether the running UI matches the brief. Report "
    "concrete, evidence-backed findings for any mismatch (wrong colour, "
    "wrong or missing element, wrong initial state, layout defect). "
    "Prefer reporting a mismatch over guessing it is acceptable. Call the "
    "record_vision_verdict tool exactly once.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA, TAG_CRITERIA_JSON))
)


def system_prompt() -> str:
    """Return the system prompt for the vision verifier LLM call."""
    return _SYSTEM_PROMPT


def build_user_text(review_input: VisionReviewInput) -> str:
    """Build the user-message text with fenced brief + criteria.

    The screenshots are attached separately as image parts; this text
    carries only the (untrusted) brief and acceptance criteria.

    Returns:
        The user-message text with the fenced brief and acceptance
        criteria.
    """
    criteria_json = json.dumps(list(review_input.acceptance_criteria))
    return (
        "Brief for the application under review:\n"
        + wrap_untrusted(TAG_TASK_DATA, review_input.brief)
        + "\n\nAcceptance criteria (JSON array):\n"
        + wrap_untrusted(TAG_CRITERIA_JSON, criteria_json)
        + "\n\nThe attached screenshots show the running application. "
        "Assess whether it matches the brief and record your verdict."
    )
