# mypy: disable-error-code="explicit-any"
"""Tests for preset validation, custom preset resolution, and warn behavior."""

from typing import TYPE_CHECKING, Any

import pytest

from synthorg.config.schema import RootConfig
from synthorg.core.enums import CompanyType
from synthorg.templates.loader import load_template_file
from synthorg.templates.presets import validate_preset_references
from synthorg.templates.renderer import render_template
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
)

if TYPE_CHECKING:
    from .conftest import TemplateFileFactory


def _make_template(
    agents: list[dict[str, Any]],
) -> CompanyTemplate:
    agent_cfgs = tuple(TemplateAgentConfig(**a) for a in agents)
    return CompanyTemplate(
        metadata=TemplateMetadata(
            name="Validation Test",
            company_type=CompanyType.CUSTOM,
        ),
        agents=agent_cfgs,
    )


# -- validate_preset_references ------------------------------------------


@pytest.mark.unit
class TestValidatePresetReferences:
    def test_no_warnings_for_builtin_presets(self) -> None:
        template = _make_template(
            [
                {"role": "CEO", "personality_preset": "visionary_leader"},
                {"role": "Dev", "personality_preset": "pragmatic_builder"},
            ]
        )
        warnings = validate_preset_references(template)
        assert warnings == ()

    def test_no_warnings_when_no_presets(self) -> None:
        template = _make_template([{"role": "Dev"}])
        warnings = validate_preset_references(template)
        assert warnings == ()

    def test_warning_for_unknown_preset(self) -> None:
        template = _make_template(
            [{"role": "Dev", "personality_preset": "totally_unknown"}]
        )
        warnings = validate_preset_references(template)
        assert len(warnings) == 1
        assert "totally_unknown" in warnings[0]
        assert "Dev" in warnings[0]

    def test_no_warning_for_custom_preset(self) -> None:
        custom: dict[str, dict[str, Any]] = {
            "my_custom": {
                "traits": ("a",),
                "communication_style": "test",
            },
        }
        template = _make_template([{"role": "Dev", "personality_preset": "my_custom"}])
        warnings = validate_preset_references(
            template,
            custom_presets=custom,
        )
        assert warnings == ()

    def test_multiple_unknown_presets(self) -> None:
        template = _make_template(
            [
                {"role": "CEO", "personality_preset": "unknown_a"},
                {"role": "Dev", "personality_preset": "unknown_b"},
                {"role": "QA", "personality_preset": "visionary_leader"},
            ]
        )
        warnings = validate_preset_references(template)
        assert len(warnings) == 2
        names = " ".join(warnings)
        assert "unknown_a" in names
        assert "unknown_b" in names

    def test_case_insensitive_validation(self) -> None:
        template = _make_template(
            [
                {"role": "CEO", "personality_preset": "VISIONARY_LEADER"},
            ]
        )
        warnings = validate_preset_references(template)
        assert warnings == ()

    def test_returns_tuple(self) -> None:
        template = _make_template([{"role": "Dev"}])
        result = validate_preset_references(template)
        assert isinstance(result, tuple)


# -- Unknown preset warning behavior ------------------------------------


@pytest.mark.unit
class TestUnknownPresetWarning:
    def test_unknown_preset_warns_and_skips_personality(self) -> None:
        """Unknown personality_preset logs a warning and omits personality."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality_preset": "does_not_exist",
        }
        result: Any = _expand_single_agent(agent, 0, set(), has_extends=False)
        assert "personality" not in result

    def test_unknown_preset_does_not_raise(self) -> None:
        """Unknown preset no longer raises TemplateRenderError."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality_preset": "nonexistent_preset",
        }
        # Should not raise -- just warns and skips personality.
        result: Any = _expand_single_agent(agent, 0, set(), has_extends=False)
        assert result["role"] == "Dev"


# -- Custom preset resolution -------------------------------------------


@pytest.mark.unit
class TestCustomPresetResolution:
    def test_custom_preset_resolved_during_expansion(self) -> None:
        """Custom preset is resolved when passed to _expand_single_agent."""
        from synthorg.templates.renderer import _expand_single_agent

        custom: dict[str, dict[str, Any]] = {
            "my_custom": {
                "traits": ("custom-trait",),
                "communication_style": "custom",
                "description": "Custom",
                "openness": 0.5,
                "conscientiousness": 0.5,
                "extraversion": 0.5,
                "agreeableness": 0.5,
                "stress_response": 0.5,
            },
        }
        agent: dict[str, object] = {
            "role": "Dev",
            "personality_preset": "my_custom",
        }
        result: Any = _expand_single_agent(
            agent,
            0,
            set(),
            has_extends=False,
            custom_presets=custom,
        )
        assert result["personality"]["communication_style"] == "custom"

    def test_custom_preset_threaded_through_render_template(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Custom presets resolve when passed to render_template."""
        yaml_content = """\
template:
  name: "Custom Preset Test"
  description: "Uses a custom preset"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
      personality_preset: "my_custom"
"""
        custom: dict[str, dict[str, Any]] = {
            "my_custom": {
                "traits": ("custom-trait",),
                "communication_style": "custom",
                "description": "Custom",
                "openness": 0.5,
                "conscientiousness": 0.5,
                "extraversion": 0.5,
                "agreeableness": 0.5,
                "stress_response": 0.5,
            },
        }
        path = tmp_template_file(yaml_content)
        loaded = load_template_file(path)
        config = render_template(loaded, custom_presets=custom)
        assert isinstance(config, RootConfig)
        agent = config.agents[0]
        assert agent.personality["communication_style"] == "custom"

    def test_unknown_custom_preset_warns_in_full_render(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Template with unknown preset renders without error."""
        yaml_content = """\
template:
  name: "Unknown Preset Test"
  description: "References nonexistent preset"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
      personality_preset: "totally_unknown"
"""
        path = tmp_template_file(yaml_content)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        agent = config.agents[0]
        assert agent.personality is None or agent.personality == {}

    def test_builtin_preset_still_works_with_custom_presets(self) -> None:
        """Builtin presets resolve when custom_presets dict is passed."""
        from synthorg.templates.renderer import _expand_single_agent

        custom: dict[str, dict[str, Any]] = {"other_custom": {"traits": ("a",)}}
        agent: dict[str, object] = {
            "role": "Dev",
            "personality_preset": "pragmatic_builder",
        }
        result: Any = _expand_single_agent(
            agent,
            0,
            set(),
            has_extends=False,
            custom_presets=custom,
        )
        assert result["personality"]["communication_style"] == "concise"
