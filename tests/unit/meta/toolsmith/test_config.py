"""Unit tests for toolsmith configuration."""

import pytest
from pydantic import ValidationError

from synthorg.meta.toolsmith.config import (
    ToolAuthoringConfig,
    ToolsmithConfig,
    ToolValidationConfig,
)
from synthorg.meta.toolsmith.models import ToolSandboxBackend

pytestmark = pytest.mark.unit


class TestToolsmithConfig:
    def test_safe_defaults(self) -> None:
        config = ToolsmithConfig()
        assert config.enabled is False
        assert config.allowed_capabilities == ()
        assert config.sandbox_backend is ToolSandboxBackend.DOCKER
        assert config.requires_network is False
        assert config.gap_recurrence_threshold >= 2
        assert config.validation.require_golden_delta is True

    def test_frozen(self) -> None:
        config = ToolsmithConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]

    def test_recurrence_threshold_floor(self) -> None:
        with pytest.raises(ValidationError):
            ToolsmithConfig(gap_recurrence_threshold=1)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ToolsmithConfig(unknown_field=1)  # type: ignore[call-arg]

    def test_nested_overrides(self) -> None:
        config = ToolsmithConfig(
            enabled=True,
            allowed_capabilities=("textkit:slugify",),
            authoring=ToolAuthoringConfig(temperature=0.1),
            validation=ToolValidationConfig(min_score_margin=5),
        )
        assert config.enabled is True
        assert config.allowed_capabilities == ("textkit:slugify",)
        assert config.authoring.temperature == pytest.approx(0.1)
        assert config.validation.min_score_margin == 5

    def test_enabled_requires_non_empty_allowlist(self) -> None:
        # enabled=True with the default empty allowlist is silently
        # deny-all; the validator rejects it so the misconfiguration
        # surfaces at boot rather than as 'tool never authored'.
        with pytest.raises(ValidationError):
            ToolsmithConfig(enabled=True, allowed_capabilities=())


class TestGoldenScorecardProviderDiscriminator:
    def test_defaults_to_none(self) -> None:
        assert ToolValidationConfig().golden_scorecard_provider == "none"

    def test_accepts_eval(self) -> None:
        config = ToolValidationConfig(golden_scorecard_provider="eval")
        assert config.golden_scorecard_provider == "eval"

    def test_rejects_unknown_arm(self) -> None:
        with pytest.raises(ValidationError):
            ToolValidationConfig(golden_scorecard_provider="bogus")  # type: ignore[arg-type]
