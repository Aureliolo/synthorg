"""Tests for seniority-tiered, family-aware model selection."""

from collections import Counter

import pytest

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.probing import parse_ollama_identity
from synthorg.templates.model_matcher import match_all_agents
from synthorg.templates.model_matcher_tiering import (
    effective_level,
    rank_by_quality,
    select_tiered,
)


def _model(  # noqa: PLR0913 -- keyword-only test factory
    model_id: str,
    *,
    parameter_count: int | None = None,
    family: str | None = None,
    generation: float | None = None,
    max_context: int = 200_000,
    tools: bool = True,
    source: MetadataSource = "probe",
) -> ProviderModelConfig:
    """Build a ProviderModelConfig with the metadata the tiering reads."""
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=0.0,
        max_context=max_context,
        metadata=ModelMetadata(
            supports_tools=tools,
            parameter_count=parameter_count,
            family=family,
            generation=generation,
            metadata_source=source,
        ),
    )


class _Provider:
    def __init__(self, *models: ProviderModelConfig) -> None:
        self.models = models


@pytest.mark.unit
class TestParseOllamaIdentity:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("deepseek-v4-pro", ("deepseek", 4.0)),
            ("glm-5.2", ("glm", 5.2)),
            ("kimi-k2.7-code", ("kimi", 2.7)),
            ("gemma4:26b-a4b-it-q4_K_M", ("gemma", 4.0)),
            ("qwen3.5:397b", ("qwen", 3.5)),
            ("nemotron-3-ultra", ("nemotron", 3.0)),
        ],
    )
    def test_extracts_family_and_generation(
        self, model_id: str, expected: tuple[str | None, float | None]
    ) -> None:
        assert parse_ollama_identity(model_id) == expected

    def test_single_letter_prefix_is_not_a_family(self) -> None:
        # Below the minimum family length -> no guess rather than a wrong one.
        assert parse_ollama_identity("x9") == (None, 9.0)


@pytest.mark.unit
class TestEffectiveLevel:
    @pytest.mark.parametrize(
        ("level", "role", "expected"),
        [
            ("mid", "CEO", "c_suite"),
            ("mid", "Chief Technology Officer", "c_suite"),
            ("mid", "CTO", "c_suite"),
            ("junior", "VP of Sales", "vp"),
            ("senior", "Backend Developer", "senior"),
            ("junior", "Engineering Lead", "lead"),
            (None, "Data Analyst", None),
        ],
    )
    def test_reconciles_level_with_role(
        self, level: str | None, role: str, expected: str | None
    ) -> None:
        assert effective_level(level, role) == expected


@pytest.mark.unit
class TestRankByQuality:
    def test_orders_strongest_first(self) -> None:
        ranked = rank_by_quality(
            [
                _model("small", parameter_count=50),
                _model("big", parameter_count=500),
                _model("mid", parameter_count=100),
            ]
        )
        assert [m.id for m in ranked] == ["big", "mid", "small"]


@pytest.mark.unit
class TestSelectTiered:
    def _ranked(self) -> list[ProviderModelConfig]:
        return rank_by_quality(
            [_model(f"m{p}", parameter_count=p) for p in (500, 400, 300, 200, 100)]
        )

    def test_executive_draws_from_the_strongest(self) -> None:
        chosen = select_tiered(self._ranked(), "c_suite", Counter())
        assert chosen is not None
        assert chosen.metadata.parameter_count == 500

    def test_junior_draws_from_the_smaller_models(self) -> None:
        chosen = select_tiered(self._ranked(), "junior", Counter())
        assert chosen is not None
        assert chosen.metadata.parameter_count is not None
        assert chosen.metadata.parameter_count <= 200

    def test_spreads_across_families_in_the_same_band(self) -> None:
        models = rank_by_quality(
            [
                _model("a1", parameter_count=100, family="alpha"),
                _model("a2", parameter_count=95, family="alpha"),
                _model("b1", parameter_count=90, family="beta"),
                _model("b2", parameter_count=85, family="beta"),
            ]
        )
        usage: Counter[str] = Counter()
        first = select_tiered(models, "mid", usage)
        assert first is not None
        assert first.metadata.family is not None
        usage[first.metadata.family] += 1
        second = select_tiered(models, "mid", usage)
        assert second is not None
        assert second.metadata.family != first.metadata.family

    def test_empty_pool_returns_none(self) -> None:
        assert select_tiered([], "mid", Counter()) is None


@pytest.mark.unit
class TestMatchAllAgentsTiered:
    def test_executive_outranks_junior_despite_level_field(self) -> None:
        models = tuple(
            _model(f"model-{p}b", parameter_count=p * 1_000_000_000)
            for p in (1600, 500, 100, 30)
        )
        providers = {"cloud": _Provider(*models)}
        agents: list[dict[str, object]] = [
            {"role": "CEO", "level": "mid"},
            {"role": "Data Analyst", "level": "junior"},
        ]
        matches = match_all_agents(agents, providers)
        by_index = {m.agent_index: m.model_id for m in matches}
        assert by_index[0] == "model-1600b"
        assert by_index[1] != by_index[0]

    def test_varied_roster_spreads_across_distinct_models(self) -> None:
        models = tuple(
            _model(
                f"fam{i}-m",
                parameter_count=(10 - i) * 1_000_000_000,
                family=f"fam{i}",
            )
            for i in range(8)
        )
        providers = {"cloud": _Provider(*models)}
        # A realistic roster spans levels; each level draws from its own band so
        # the assignment fans out across the catalogue instead of stacking.
        levels = ["c_suite", "senior", "mid", "mid", "junior", "junior"]
        agents: list[dict[str, object]] = [
            {"role": f"Role {i}", "level": lv} for i, lv in enumerate(levels)
        ]
        matches = match_all_agents(agents, providers)
        chosen = {m.model_id for m in matches}
        assert len(chosen) >= 4
