"""Checkpoint promotion gate for the continual-improvement finetune (#1990).

A new embedding checkpoint is promoted (activated as the live embedder) ONLY
when a measured A/B shows it beats the incumbent by at least a margin. The gate
itself is a pure, signal-agnostic decision over two scores: the orchestrator
feeds it the eval-stage NDCG@10 A/B (fine-tuned vs base model), while the
golden-benchmark sim-harness feeds it two ``Scorecard.total`` values. Keeping it
pure is what lets ``src`` decide promotion without importing the ``evals``
benchmark layer -- whoever can run the comparison supplies the scores.

The margin is strictly positive by construction: a tie is not a win, so it must
never promote.
"""

from typing import Final

# Minimum absolute score gain (in the caller's own units) required to promote.
# For the orchestrator this is an NDCG@10 delta; the golden-benchmark harness
# passes its own points-scale margin. A small positive default keeps a tie or a
# regression from ever activating a checkpoint.
DEFAULT_PROMOTION_MARGIN: Final[float] = 0.01


def should_promote(
    base_score: float,
    candidate_score: float,
    *,
    margin: float = DEFAULT_PROMOTION_MARGIN,
) -> bool:
    """Decide whether a candidate checkpoint beats the incumbent.

    Args:
        base_score: Measured score of the incumbent (active) model.
        candidate_score: Measured score of the fine-tuned candidate, in the
            same units as ``base_score``.
        margin: Minimum gain (``candidate_score - base_score``) required to
            promote. Must be strictly positive so a tie can never promote.

    Returns:
        ``True`` only when the candidate beats the base by at least ``margin``.

    Raises:
        ValueError: If ``margin`` is not strictly positive.
    """
    if margin <= 0.0:
        msg = "margin must be positive so a tie does not promote"
        raise ValueError(msg)
    return candidate_score - base_score >= margin
