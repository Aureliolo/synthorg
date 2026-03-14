"""Tests for CoordinationSectionConfig."""

import pytest

from synthorg.core.enums import CoordinationTopology
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.section_config import (
    CoordinationSectionConfig,
)
from synthorg.engine.routing.models import AutoTopologyConfig

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCoordinationSectionConfigDefaults:
    """Default values match the plan specification."""

    def test_default_topology_is_auto(self) -> None:
        cfg = CoordinationSectionConfig()
        assert cfg.topology == CoordinationTopology.AUTO

    def test_default_max_concurrency_is_none(self) -> None:
        cfg = CoordinationSectionConfig()
        assert cfg.max_concurrency_per_wave is None

    def test_default_fail_fast_is_false(self) -> None:
        cfg = CoordinationSectionConfig()
        assert cfg.fail_fast is False

    def test_default_enable_workspace_isolation_is_true(self) -> None:
        cfg = CoordinationSectionConfig()
        assert cfg.enable_workspace_isolation is True

    def test_default_base_branch_is_main(self) -> None:
        cfg = CoordinationSectionConfig()
        assert cfg.base_branch == "main"

    def test_default_auto_topology_rules(self) -> None:
        cfg = CoordinationSectionConfig()
        assert isinstance(cfg.auto_topology_rules, AutoTopologyConfig)

    def test_frozen_model(self) -> None:
        from pydantic import ValidationError

        cfg = CoordinationSectionConfig()
        with pytest.raises(ValidationError):
            cfg.fail_fast = True  # type: ignore[misc]


@pytest.mark.unit
class TestCoordinationSectionConfigToCoordinationConfig:
    """to_coordination_config() produces correct CoordinationConfig."""

    def test_default_conversion(self) -> None:
        cfg = CoordinationSectionConfig()
        result = cfg.to_coordination_config()
        assert isinstance(result, CoordinationConfig)
        assert result.max_concurrency_per_wave is None
        assert result.fail_fast is False
        assert result.enable_workspace_isolation is True
        assert result.base_branch == "main"

    def test_custom_values_carried_through(self) -> None:
        cfg = CoordinationSectionConfig(
            max_concurrency_per_wave=4,
            fail_fast=True,
            enable_workspace_isolation=False,
            base_branch="develop",
        )
        result = cfg.to_coordination_config()
        assert result.max_concurrency_per_wave == 4
        assert result.fail_fast is True
        assert result.enable_workspace_isolation is False
        assert result.base_branch == "develop"

    def test_request_overrides_take_precedence(self) -> None:
        cfg = CoordinationSectionConfig(
            max_concurrency_per_wave=4,
            fail_fast=False,
        )
        result = cfg.to_coordination_config(
            max_concurrency_per_wave=8,
            fail_fast=True,
        )
        assert result.max_concurrency_per_wave == 8
        assert result.fail_fast is True

    def test_none_overrides_use_section_defaults(self) -> None:
        cfg = CoordinationSectionConfig(
            max_concurrency_per_wave=4,
            fail_fast=True,
        )
        result = cfg.to_coordination_config(
            max_concurrency_per_wave=None,
            fail_fast=None,
        )
        assert result.max_concurrency_per_wave == 4
        assert result.fail_fast is True


@pytest.mark.unit
class TestCoordinationSectionConfigValidation:
    """Validation constraints on CoordinationSectionConfig."""

    def test_max_concurrency_must_be_positive(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CoordinationSectionConfig(max_concurrency_per_wave=0)

    def test_base_branch_must_not_be_blank(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CoordinationSectionConfig(base_branch="  ")

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="extra"):
            CoordinationSectionConfig(unknown_field="oops")  # type: ignore[call-arg]
