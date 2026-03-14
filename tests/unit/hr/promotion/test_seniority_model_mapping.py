"""Unit tests for SeniorityModelMapping strategy."""

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.promotion.config import ModelMappingConfig
from synthorg.hr.promotion.seniority_model_mapping import (
    SeniorityModelMapping,
)

from .conftest import make_agent_identity

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.unit
class TestSeniorityModelMappingName:
    """Tests for strategy identity."""

    def test_name(
        self,
        model_mapping_config: ModelMappingConfig,
    ) -> None:
        """Strategy name is 'seniority_model_mapping'."""
        strategy = SeniorityModelMapping(config=model_mapping_config)
        assert strategy.name == "seniority_model_mapping"


@pytest.mark.unit
class TestSeniorityModelMappingResolve:
    """Tests for resolve_model behavior."""

    def test_returns_new_tier_on_level_change(
        self,
        model_mapping_config: ModelMappingConfig,
    ) -> None:
        """resolve_model returns the new tier when level changes.

        A Junior agent with model 'test-small-001' promoted to Mid
        should get the 'medium' tier from the role catalog.
        """
        strategy = SeniorityModelMapping(config=model_mapping_config)
        identity = make_agent_identity(
            level=SeniorityLevel.JUNIOR,
            model_id="small",
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=SeniorityLevel.MID,
        )
        # Role catalog: MID -> "medium"
        assert result == "medium"

    def test_returns_none_when_disabled(self) -> None:
        """resolve_model returns None when model_follows_seniority=False."""
        config = ModelMappingConfig(model_follows_seniority=False)
        strategy = SeniorityModelMapping(config=config)
        identity = make_agent_identity(
            level=SeniorityLevel.JUNIOR,
            model_id="small",
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=SeniorityLevel.MID,
        )
        assert result is None

    def test_uses_explicit_override(self) -> None:
        """resolve_model uses explicit seniority_model_map override."""
        config = ModelMappingConfig(
            seniority_model_map={
                "senior": "test-custom-large",
            },
        )
        strategy = SeniorityModelMapping(config=config)
        identity = make_agent_identity(
            level=SeniorityLevel.MID,
            model_id="test-small-001",
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=SeniorityLevel.SENIOR,
        )
        assert result == "test-custom-large"

    def test_returns_none_when_model_unchanged(
        self,
        model_mapping_config: ModelMappingConfig,
    ) -> None:
        """resolve_model returns None when current model matches tier.

        Role catalog: MID -> "medium". If agent already uses "medium",
        no change is needed.
        """
        strategy = SeniorityModelMapping(config=model_mapping_config)
        identity = make_agent_identity(
            level=SeniorityLevel.JUNIOR,
            model_id="medium",
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=SeniorityLevel.MID,
        )
        assert result is None

    def test_explicit_override_same_model_returns_none(self) -> None:
        """Override map match but same model returns None."""
        config = ModelMappingConfig(
            seniority_model_map={"senior": "test-small-001"},
        )
        strategy = SeniorityModelMapping(config=config)
        identity = make_agent_identity(
            level=SeniorityLevel.MID,
            model_id="test-small-001",
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=SeniorityLevel.SENIOR,
        )
        assert result is None

    @pytest.mark.parametrize(
        ("current_model", "new_level", "expected_tier"),
        [
            pytest.param("small", SeniorityLevel.SENIOR, "medium", id="jr-to-sr"),
            pytest.param("small", SeniorityLevel.LEAD, "large", id="jr-to-lead"),
            pytest.param(
                "medium",
                SeniorityLevel.PRINCIPAL,
                "large",
                id="mid-to-principal",
            ),
        ],
    )
    def test_tier_resolution_parametrized(
        self,
        current_model: str,
        new_level: SeniorityLevel,
        expected_tier: str,
        model_mapping_config: ModelMappingConfig,
    ) -> None:
        """Parametrized tier resolution from role catalog."""
        strategy = SeniorityModelMapping(config=model_mapping_config)
        identity = make_agent_identity(
            level=SeniorityLevel.JUNIOR,
            model_id=current_model,
        )
        result = strategy.resolve_model(
            agent_identity=identity,
            new_level=new_level,
        )
        assert result == expected_tier
