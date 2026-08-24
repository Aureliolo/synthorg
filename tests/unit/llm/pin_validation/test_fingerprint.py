"""Unit tests for the pin fingerprint helpers + golden loader.

Covers: fingerprint determinism, the golden loader's absent (empty),
malformed (ValueError), and valid paths via the ``path`` injection seam,
and the drift diff.
"""

import json
from pathlib import Path

import pytest

from synthorg.llm.pin_validation import (
    golden_diff,
    load_pin_golden,
    pin_fingerprint,
)

pytestmark = pytest.mark.unit


def _fp(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "model_id": "example-basic-001",
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 1024,
        "output": "OK",
    }
    kwargs.update(overrides)
    return pin_fingerprint(**kwargs)  # type: ignore[arg-type]


def test_fingerprint_is_deterministic() -> None:
    assert _fp() == _fp()


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_id": "example-expert-001"},
        {"temperature": 0.5},
        {"top_p": 0.9},
        {"max_tokens": 2048},
        {"output": "different"},
    ],
)
def test_fingerprint_changes_with_each_pin_field(overrides: dict[str, object]) -> None:
    assert _fp(**overrides) != _fp()


def test_load_golden_absent_returns_empty(tmp_path: Path) -> None:
    assert load_pin_golden(tmp_path / "missing.json") == {}


def test_load_golden_valid(tmp_path: Path) -> None:
    artifact = tmp_path / "g.json"
    artifact.write_text(json.dumps({"system:memory:rerank": "abc"}), encoding="utf-8")
    assert load_pin_golden(artifact) == {"system:memory:rerank": "abc"}


def test_load_golden_invalid_json_raises(tmp_path: Path) -> None:
    artifact = tmp_path / "g.json"
    artifact.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_pin_golden(artifact)


def test_load_golden_non_str_map_raises(tmp_path: Path) -> None:
    artifact = tmp_path / "g.json"
    artifact.write_text(json.dumps({"k": 123}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_pin_golden(artifact)


def test_golden_diff_flags_absent_and_changed() -> None:
    live = {"a": "1", "b": "2", "c": "3"}
    golden = {"a": "1", "b": "X"}  # b changed, c absent from golden
    assert golden_diff(live, golden) == ("b", "c")


def test_golden_diff_clean_when_identical() -> None:
    live = {"a": "1", "b": "2"}
    assert golden_diff(live, live) == ()
