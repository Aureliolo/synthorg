"""Tests for named operating postures and their feature-flag expansion."""

import pytest
from pydantic import ValidationError

from synthorg.config.posture_config import PostureConfig
from synthorg.templates.enums import PostureName
from synthorg.templates.errors import TemplatePostureError
from synthorg.templates.postures import (
    NamedBundlePostureStrategy,
    PostureExpansionStrategy,
    get_posture_strategy,
)


@pytest.mark.unit
class TestPostureConfig:
    def test_default_is_all_off(self) -> None:
        bundle = PostureConfig()
        assert bundle.name is None
        assert bundle.knowledge_substrate is False
        assert bundle.chat_propose is False
        assert bundle.group_chat is False
        assert bundle.steering is False
        assert bundle.red_team is False
        assert bundle.red_team_grounding == "heuristic"
        assert bundle.auto_downgrade is False

    def test_frozen(self) -> None:
        bundle = PostureConfig()
        with pytest.raises(ValidationError):
            bundle.steering = True  # type: ignore[misc]

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            PostureConfig(unknown=True)  # type: ignore[call-arg]

    def test_merge_ors_flags_keeps_host_name(self) -> None:
        a = PostureConfig(name="host", red_team=True)
        b = PostureConfig(name="pack", group_chat=True, steering=True)
        merged = a.merge(b)
        assert merged.name == "host"
        assert merged.red_team is True
        assert merged.group_chat is True
        assert merged.steering is True

    def test_merge_upgrades_grounding(self) -> None:
        a = PostureConfig(red_team=True, red_team_grounding="heuristic")
        b = PostureConfig(red_team=True, red_team_grounding="knowledge_substrate")
        assert a.merge(b).red_team_grounding == "knowledge_substrate"
        assert b.merge(a).red_team_grounding == "knowledge_substrate"

    def test_merge_keeps_heuristic_when_neither_upgrades(self) -> None:
        a = PostureConfig()
        b = PostureConfig(steering=True)
        assert a.merge(b).red_team_grounding == "heuristic"

    def test_grounding_without_red_team_rejected(self) -> None:
        with pytest.raises(ValidationError, match="red_team"):
            PostureConfig(red_team_grounding="knowledge_substrate")


@pytest.mark.unit
class TestNamedBundleStrategy:
    @pytest.mark.parametrize("name", list(PostureName))
    def test_every_posture_expands(self, name: PostureName) -> None:
        strategy = NamedBundlePostureStrategy()
        bundle = strategy.expand(name)
        assert isinstance(bundle, PostureConfig)
        assert bundle.name == name.value

    def test_autonomous_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(PostureName.AUTONOMOUS)
        assert bundle.steering is True
        assert bundle.knowledge_substrate is True
        assert bundle.group_chat is False

    def test_supervised_client_facing_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(
            PostureName.SUPERVISED_CLIENT_FACING,
        )
        assert bundle.group_chat is True
        assert bundle.agent_invite is True
        assert bundle.red_team is False

    def test_security_hardened_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(PostureName.SECURITY_HARDENED)
        assert bundle.red_team is True
        assert bundle.red_team_grounding == "knowledge_substrate"
        assert bundle.direct_mcp is False

    def test_cost_disciplined_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(PostureName.COST_DISCIPLINED)
        assert bundle.auto_downgrade is True
        assert bundle.knowledge_substrate is False
        assert bundle.chat_propose is False

    def test_knowledge_heavy_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(PostureName.KNOWLEDGE_HEAVY)
        assert bundle.knowledge_substrate is True
        assert bundle.chat_propose is True
        assert bundle.steering is True
        assert bundle.red_team is False

    def test_research_autonomous_bundle(self) -> None:
        bundle = NamedBundlePostureStrategy().expand(PostureName.RESEARCH_AUTONOMOUS)
        assert bundle.knowledge_substrate is True
        assert bundle.chat_propose is True
        assert bundle.steering is True

    def test_unknown_posture_raises(self) -> None:
        from types import MappingProxyType

        empty = NamedBundlePostureStrategy(MappingProxyType({}))
        with pytest.raises(TemplatePostureError, match="No feature bundle"):
            empty.expand(PostureName.AUTONOMOUS)


@pytest.mark.unit
class TestPostureFactory:
    def test_default_kind_is_named(self) -> None:
        strategy = get_posture_strategy()
        assert isinstance(strategy, NamedBundlePostureStrategy)

    def test_satisfies_protocol(self) -> None:
        assert isinstance(get_posture_strategy(), PostureExpansionStrategy)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(TemplatePostureError, match="Unknown posture strategy"):
            get_posture_strategy("bogus")
