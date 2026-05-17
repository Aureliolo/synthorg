"""Hermetic unit tests for the Postgres preset repo JSONB adapter.

``custom_presets.config_json`` is a JSONB column while the protocol
carries ``config_json`` as JSON source text; ``_config_json_to_jsonb``
bridges the two by parsing then wrapping so the value is stored
structurally rather than as a JSON string scalar.
"""

import json

import pytest
from psycopg.types.json import Jsonb

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.postgres.preset_repo import (
    _config_json_to_jsonb,
    _normalize_config_json,
)

pytestmark = pytest.mark.unit


def test_config_json_to_jsonb_parses_object() -> None:
    """A valid JSON object string is parsed and wrapped for JSONB."""
    wrapped = _config_json_to_jsonb('{"a": 1, "b": ["x"]}')

    assert isinstance(wrapped, Jsonb)
    assert wrapped.obj == {"a": 1, "b": ["x"]}


def test_config_json_to_jsonb_round_trips_via_normalize() -> None:
    """Parsed-then-normalized config text equals the original payload."""
    payload = {"models": [{"name": "m1"}], "n": 2}
    wrapped = _config_json_to_jsonb(json.dumps(payload))

    assert json.loads(_normalize_config_json(wrapped.obj)) == payload


@pytest.mark.parametrize(
    "raw",
    ["not json", "", "{unclosed", "undefined"],
)
def test_config_json_to_jsonb_rejects_invalid_json(raw: str) -> None:
    """Invalid JSON surfaces as the domain ``QueryError``, not a 500."""
    with pytest.raises(QueryError):
        _config_json_to_jsonb(raw)
