"""Tests for the verifier-harness template pack."""

from pathlib import Path
from typing import TypedDict, cast

import pytest
import yaml


class _PackAgent(TypedDict):
    """The agent keys this pack's assertions navigate."""

    role: str
    model: dict[str, object]


class _PackTemplate(TypedDict):
    """The template keys this pack's assertions navigate."""

    agents: list[_PackAgent]
    tags: list[str]
    min_agents: int
    max_agents: int
    workflow: str
    communication: str


class _Pack(TypedDict):
    template: _PackTemplate


def _load_pack() -> _Pack:
    pack_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "synthorg"
        / "templates"
        / "packs"
        / "verifier-harness.yaml"
    )
    with pack_path.open() as f:
        return cast("_Pack", yaml.safe_load(f))


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

    def test_evaluator_outranks_the_generator_it_judges(self) -> None:
        """The judge is bound to a reasoning model; the generator need not be."""
        data = _load_pack()
        agents = data["template"]["agents"]
        evaluator = next(a for a in agents if a["role"] == "Evaluator")
        generator = next(a for a in agents if a["role"] == "Generator")
        assert evaluator["model"]["requires_reasoning"] is True
        assert "requires_reasoning" not in generator["model"]

    def test_has_verification_tag(self) -> None:
        data = _load_pack()
        tags = data["template"]["tags"]
        assert "verification" in tags

    def test_min_max_agents(self) -> None:
        data = _load_pack()
        assert data["template"]["min_agents"] == 3
        assert data["template"]["max_agents"] == 3

    def test_harness_contract_fields(self) -> None:
        data = _load_pack()
        assert data["template"]["workflow"] == "sequential_pipeline"
        assert data["template"]["communication"] == "structured"

    def test_every_agent_states_its_model(self) -> None:
        """Each agent is a bound unit, so each names its own requirement."""
        data = _load_pack()
        for agent in data["template"]["agents"]:
            assert agent["model"], f"Agent {agent['role']!r} states no model"
