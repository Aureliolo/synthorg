# module-kind: code
"""Deterministic auto-rewrite for the output-style policy.

Used only by rules in ``AUTO_REWRITE`` mode (off in the default pack). A rewrite
is applied to a prose span only; the evaluator never emits a rewrite op inside a
code span, so this module cannot corrupt code. Operations are applied to the
original text right-to-left so earlier offsets stay valid.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


class RewriteOp(BaseModel):
    """A single deterministic replacement over a span of the original output.

    Attributes:
        start: Inclusive start offset into the original output.
        end: Exclusive end offset into the original output.
        replacement: The text that replaces ``output[start:end]``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str


def apply_rewrites(text: str, ops: Sequence[RewriteOp]) -> str:
    """Apply non-overlapping rewrite operations to ``text``.

    Args:
        text: The original agent output.
        ops: Replacement operations; overlaps are resolved by applying the
            earliest-starting op and skipping any later op it overlaps.

    Returns:
        The rewritten text (unchanged when ``ops`` is empty).
    """
    if not ops:
        return text
    ordered = sorted(ops, key=lambda op: (op.start, op.end))
    result: list[str] = []
    cursor = 0
    for op in ordered:
        if op.start < cursor:
            continue
        result.append(text[cursor : op.start])
        result.append(op.replacement)
        cursor = op.end
    result.append(text[cursor:])
    return "".join(result)


__all__ = ["RewriteOp", "apply_rewrites"]
