"""Anchor-set loader for judged briefs.

An anchor set is a calibration corpus: a small list of hand-scored
reference outputs per rubric. At scoring time the judge re-scores
these anchors and we check that the ordering agrees with the hand
scores (Spearman rho >= gate). The anchor set lives at
``evals/anchors/<rubric_id>.yaml``.
"""

from typing import TYPE_CHECKING, Final, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from evals.errors import JudgeAnchorSetTooSmallError
from synthorg.api.boundary import parse_typed
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

# Minimum anchor set size for a Spearman correlation to be meaningful.
# Aligned with evals.scoring.spearman.MIN_PAIRS_FOR_CORRELATION but
# enforced one level up so a clearly-undersized anchor set fails at
# load time rather than during scoring.
MIN_ANCHOR_SET_SIZE: Final[int] = 5


class AnchorItem(BaseModel):
    """One hand-scored example in the anchor set."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    anchor_id: NotBlankStr
    output: NotBlankStr
    hand_scores: dict[NotBlankStr, float] = Field(min_length=1)


class AnchorSet(BaseModel):
    """A complete anchor set for one rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rubric_id: NotBlankStr
    anchor_set_version: int = Field(ge=1)
    items: tuple[AnchorItem, ...] = Field(min_length=1)


def load_anchor_set(anchors_dir: Path, rubric_id: str) -> AnchorSet:
    """Load the anchor set for *rubric_id* from *anchors_dir*.

    Args:
        anchors_dir: Directory holding ``<rubric_id>.yaml`` files.
        rubric_id: Identifier of the anchor set to load; the file
            ``<rubric_id>.yaml`` must exist.

    Returns:
        Validated :class:`AnchorSet`.

    Raises:
        FileNotFoundError: If the expected anchor file is missing.
        JudgeAnchorSetTooSmallError: If the set carries fewer than
            :data:`MIN_ANCHOR_SET_SIZE` items.
        pydantic.ValidationError: On schema violations.
    """
    path = anchors_dir / f"{rubric_id}.yaml"
    if not path.is_file():
        msg = f"Anchor set file not found: {path}"
        raise FileNotFoundError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Anchor file {path.name!r}: top-level YAML must be a mapping"
        raise TypeError(msg)
    anchor_set = parse_typed(
        "evals.anchor_set", cast("dict[str, object]", raw), AnchorSet
    )
    if len(anchor_set.items) < MIN_ANCHOR_SET_SIZE:
        msg = (
            f"Anchor set {rubric_id!r} has {len(anchor_set.items)} items; "
            f"minimum required is {MIN_ANCHOR_SET_SIZE}"
        )
        raise JudgeAnchorSetTooSmallError(msg)
    if anchor_set.rubric_id != rubric_id:
        msg = (
            f"Anchor file {path.name!r} declares rubric_id={anchor_set.rubric_id!r}, "
            f"expected {rubric_id!r}"
        )
        raise ValueError(msg)
    return anchor_set


__all__ = ["MIN_ANCHOR_SET_SIZE", "AnchorItem", "AnchorSet", "load_anchor_set"]
