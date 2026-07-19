"""Deterministic safety checks for operator-authored regex patterns.

A ``REGEX_BAN`` rule's pattern is authored by an operator (or a user pack) and
then run over all agent output at every boundary. Python's ``re`` has no
execution-time limit, so a catastrophic-backtracking pattern would hang the
whole agent pipeline. Reject the worst nested-quantifier constructs and confirm
the pattern compiles at pack-load time, so a bad pattern fails loudly where it
is defined rather than silently on the first boundary check.

The nested-quantifier screen is a conservative heuristic, not a proof of linear
matching: it rejects the classic ``(a+)+`` / ``(a*)*`` / ``(.*)+`` shapes that
cause exponential backtracking, which is the realistic operator-error case.
"""

import re
from typing import Final

_MAX_PATTERN_LEN: Final[int] = 512

#: A group that contains an unbounded quantifier and is itself unbounded-quantified
#: (or repeated ``{n,}``): the classic exponential-backtracking construct.
_NESTED_QUANTIFIER: Final[re.Pattern[str]] = re.compile(
    r"\([^()]*[+*][^()]*\)[+*]|\([^()]*[+*][^()]*\)\{\d*,\}"
)


def assert_regex_safe(pattern: str) -> None:
    """Reject a pattern that is oversized or prone to catastrophic backtracking.

    Args:
        pattern: The raw regex source from a pack rule.

    Raises:
        ValueError: When the pattern is too long or contains a nested
            unbounded-quantifier construct.
    """
    if len(pattern) > _MAX_PATTERN_LEN:
        msg = f"regex pattern too long ({len(pattern)} > {_MAX_PATTERN_LEN} chars)"
        raise ValueError(msg)
    if _NESTED_QUANTIFIER.search(pattern):
        msg = (
            f"regex {pattern!r} has a nested unbounded quantifier "
            "(catastrophic-backtracking risk); rewrite without a quantified group "
            "inside another quantifier"
        )
        raise ValueError(msg)


def compile_checked(pattern: str, *, case_insensitive: bool) -> re.Pattern[str]:
    """Validate and compile a ``REGEX_BAN`` pattern.

    Args:
        pattern: The raw regex source from a pack rule.
        case_insensitive: Whether to compile with ``re.IGNORECASE``.

    Returns:
        The compiled pattern.

    Raises:
        ValueError: When the pattern is unsafe (see :func:`assert_regex_safe`)
            or does not compile.
    """
    assert_regex_safe(pattern)
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        msg = f"invalid regex {pattern!r}: {exc}"
        raise ValueError(msg) from exc


__all__ = ["assert_regex_safe", "compile_checked"]
