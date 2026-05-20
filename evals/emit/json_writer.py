"""Write a :class:`Scorecard` to disk as canonical JSON.

Pydantic's :func:`model_dump_json` already produces a stable
representation; this module adds the file-write side and a small
contract: ``scorecard.json`` lands in *out_dir* (created if missing),
overwriting any prior file atomically via ``write_text``.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from evals.models.scorecard import Scorecard

logger = get_logger(__name__)

SCORECARD_JSON_FILENAME: str = "scorecard.json"
JSON_INDENT: int = 2


def write_scorecard_json(scorecard: Scorecard, out_dir: Path) -> Path:
    """Write *scorecard* as JSON into *out_dir*; return the file path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SCORECARD_JSON_FILENAME
    payload = scorecard.model_dump_json(indent=JSON_INDENT)
    target.write_text(payload + "\n", encoding="utf-8")
    return target


__all__ = ["SCORECARD_JSON_FILENAME", "write_scorecard_json"]
