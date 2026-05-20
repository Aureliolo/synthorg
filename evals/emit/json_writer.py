"""Write a :class:`Scorecard` to disk as canonical JSON.

Pydantic's :func:`model_dump_json` already produces a stable
representation; this module adds the file-write side and a small
contract: ``scorecard.json`` lands in *out_dir* (created if missing),
overwriting any prior file atomically via a same-directory tempfile +
:meth:`pathlib.Path.replace`. ``Path.write_text`` is not atomic (it
truncates the target on open), so a crash mid-write could leave a
partial scorecard on disk; this matters because downstream consumers
parse the JSON file out-of-band and would happily ingest a truncated
payload.
"""

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from evals.models.scorecard import Scorecard

logger = get_logger(__name__)

SCORECARD_JSON_FILENAME: str = "scorecard.json"
JSON_INDENT: int = 2


def write_scorecard_json(scorecard: Scorecard, out_dir: Path) -> Path:
    """Write *scorecard* as JSON into *out_dir* atomically; return the file path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SCORECARD_JSON_FILENAME
    payload = scorecard.model_dump_json(indent=JSON_INDENT) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=out_dir,
        encoding="utf-8",
        prefix=f".{SCORECARD_JSON_FILENAME}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)

    try:
        temp_path.replace(target)
    except OSError:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise
    return target


__all__ = ["SCORECARD_JSON_FILENAME", "write_scorecard_json"]
