"""Patterns for asserting on the shape of a serialised API response.

Both live here rather than in each test module because both encode a
claim that is made in one place and relied on in several: the RFC 9457
``instance`` field is a UUID4, and no other field of a response may hold
a run long enough for a payment-card matcher to consider it. A copy per
test file drifts the moment either claim is revised.
"""

import re
from typing import Final

UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
)

# The shortest run a payment-card matcher will consider: a Maestro
# number starts at 12 digits, which is also exactly the width of a
# dashed UUID's final group. That coincidence is what a DAST scan
# reported as a credit card; see .github/zap-rules.tsv, rule 10062.
CARD_SHAPED_DIGIT_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\d{12,}")


def assert_no_card_shaped_run(body: str, *, instance: str | None = None) -> None:
    """Assert *body* holds no card-shaped digit run outside its ``instance``.

    Args:
        body: The serialised response text.
        instance: The correlation id to discount, which is the one field
            legitimately able to carry such a run. ``None`` for a body
            that carries no ``instance`` at all, such as a success
            payload, where the whole text is then held to the rule.

    Raises:
        AssertionError: If a run of 12 or more digits survives removing
            *instance*, naming the run so the reader does not have to
            map a character offset back to a field.
    """
    remainder = body.replace(instance, "") if instance else body
    match = CARD_SHAPED_DIGIT_RUN_RE.search(remainder)
    if match is not None:
        msg = (
            f"card-shaped digit run {match.group()!r} in a response field "
            f"other than instance; rule 10062 is suppressed on the premise "
            f"that only the correlation id can hold one. Body: {remainder}"
        )
        raise AssertionError(msg)
