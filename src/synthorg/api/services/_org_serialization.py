"""Shared JSON serialization helpers for org mutation services."""

import json
from collections.abc import Sequence

from pydantic import BaseModel


def json_dump_models(models: Sequence[BaseModel]) -> str:
    """Serialize a sequence of Pydantic models to compact JSON.

    Returns:
        Resulting string.
    """
    return json.dumps(
        [m.model_dump(mode="json") for m in models],
        separators=(",", ":"),
    )
