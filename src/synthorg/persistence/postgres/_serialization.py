"""Postgres-side serialization helpers.

The Postgres schema stores JSON-shaped fields as ``JSONB`` and
datetime fields as ``TIMESTAMPTZ``, so repositories pass native Python
values (``dict``, ``list``, ``datetime``, ``bool``) through psycopg's
type adapters rather than serializing to strings like the SQLite
backend does.

These helpers convert tuples of Pydantic models into lists of plain
Python dicts that psycopg can adapt to ``JSONB`` wire format.  They
are the Postgres-side counterpart to ``_json_list`` in
``synthorg.persistence.sqlite.repositories``.
"""

from typing import Any

from pydantic import BaseModel

from synthorg.observability import get_logger

logger = get_logger(__name__)


def jsonify(value: object) -> Any:
    """Convert a value to a JSON-shaped Python structure.

    - Pydantic ``BaseModel`` instances are serialized via
      ``model_dump(mode="json")`` to get a plain dict with
      JSON-compatible leaf values (datetimes as ISO strings, enums
      as their value, etc.).
    - Dicts, lists, and scalar types pass through unchanged -- psycopg
      adapts them directly to ``JSONB``.

    The return type is ``Any`` because psycopg's adapter accepts
    arbitrary JSON-compatible Python structures.

    Returns:
        Result of type ``Any``.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def jsonify_list(items: tuple[object, ...] | list[object]) -> list[Any]:
    """Serialize a tuple or list of values to a JSONB-ready list.

    Each element is passed through :func:`jsonify`, so Pydantic models
    become dicts while scalars and plain containers pass through.

    Returns an empty list for an empty input (the common "no
    reviewers yet" / "no dependencies yet" case).

    Returns:
        The matching collection.
    """
    return [jsonify(item) for item in items]


__all__ = ["jsonify", "jsonify_list"]
