"""Shared fixtures for the eval-spine test suite."""

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

BriefYamlWriter = Callable[..., Path]  # type: ignore[explicit-any]  # arbitrary-arg brief-writer test factory


def _brief_yaml(kind: str, **overrides: object) -> str:
    """Produce a minimal valid brief YAML string for tests.

    Either branch (executable / judged) ships the matching block. Tests
    pass field overrides via *overrides* to construct invalid payloads.
    """
    base: dict[str, object] = {
        "brief_id": overrides.pop("brief_id", "BRIEF_TEST_001"),
        "schema_version": 1,
        "kind": kind,
        "title": "Test brief",
        "description": "A brief used in unit tests.",
        "priority": "medium",
        "estimated_complexity": 3,
        "acceptance_criteria": ["criterion one"],
        "limits": {
            "max_total_cost_usd": 1.0,
            "max_wall_clock_seconds": 60,
            "max_turns": 8,
        },
    }
    if kind == "executable":
        base["checks"] = {
            "hidden_tests": [{"cmd": ["echo", "ok"], "timeout_seconds": 5}],
        }
    else:
        base["rubric"] = {
            "rubric_id": "summarise",
            "dimensions": [
                {"name": "faithfulness", "weight": 0.5, "grade_type": "ternary"},
                {"name": "clarity", "weight": 0.5, "grade_type": "ternary"},
            ],
            "reference_answer_path": "anchors/summarise_reference.md",
        }
    base.update(overrides)
    return yaml.safe_dump(base, sort_keys=False)


@pytest.fixture
def write_brief_yaml(tmp_path: Path) -> BriefYamlWriter:
    """Return a helper that writes a brief YAML into *tmp_path*."""

    def _write(filename: str, kind: str, **overrides: object) -> Path:
        path = tmp_path / filename
        path.write_text(_brief_yaml(kind, **overrides), encoding="utf-8")
        return path

    return _write
