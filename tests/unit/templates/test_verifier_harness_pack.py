# mypy: disable-error-code="explicit-any"
"""Tests for the verifier-harness template pack."""

from pathlib import Path
from typing import Any

import pytest
import yaml


def _load_pack() -> dict[str, Any]:
    pack_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "synthorg"
        / "templates"
        / "packs"
        / "verifier-harness.yaml"
    )
    with pack_path.open() as f:
        result: dict[str, Any] = yaml.safe_load(f)
    return result


@pytest.mark.unit
class TestVerifierHarnessPack:
    def test_has_three_agents(self) -> None:
        data = _load_pack()
        agents = data["template"]["agents"]
        assert len(agents) == 3

    def test_agent_roles(self) -> None:
        data = _load_pack()
        roles = {a["role"] for a in data["template"]["agents"]}
        assert roles == {"Planner", "Generator", "Evaluator"}

    def test_evaluator_and_generator_have_different_presets(self) -> None:
        data = _load_pack()
        agents = data["template"]["agents"]
        evaluator = next(a for a in agents if a["role"] == "Evaluator")
        generator = next(a for a in agents if a["role"] == "Generator")
        assert evaluator["personality_preset"] != generator["personality_preset"]

    def test_has_verification_tag(self) -> None:
        data = _load_pack()
        tags = data["template"]["tags"]
        assert "verification" in tags

    def test_min_max_agents(self) -> None:
        data = _load_pack()
        assert data["template"]["min_agents"] == 3
        assert data["template"]["max_agents"] == 3

    def test_evaluator_uses_quality_guardian_preset(self) -> None:
        data = _load_pack()
        agents = data["template"]["agents"]
        evaluator = next(a for a in agents if a["role"] == "Evaluator")
        assert evaluator["personality_preset"] == "quality_guardian"

    def test_harness_contract_fields(self) -> None:
        data = _load_pack()
        assert data["template"]["workflow"] == "sequential_pipeline"
        assert data["template"]["communication"] == "structured"

    def test_all_presets_are_valid(self) -> None:
        from synthorg.templates.presets import PERSONALITY_PRESETS

        data = _load_pack()
        for agent in data["template"]["agents"]:
            preset = agent["personality_preset"]
            assert preset in PERSONALITY_PRESETS, (
                f"Agent {agent['role']!r} uses unknown preset {preset!r}"
            )
