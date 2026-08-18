"""Section-config seams flow into the per-run CoordinationConfig."""

import pytest

from synthorg.engine.coordination.section_config import CoordinationSectionConfig

pytestmark = pytest.mark.unit


class TestSectionConfigDefaults:
    """Defaults preserve current behaviour."""

    def test_middleware_disabled_by_default(self) -> None:
        assert CoordinationSectionConfig().enable_coordination_middleware is False

    def test_max_delegation_rounds_default_3(self) -> None:
        assert CoordinationSectionConfig().max_delegation_rounds == 3


class TestToCoordinationConfigThreading:
    """to_coordination_config carries the seams to the per-run model."""

    def test_threads_delegation_cap(self) -> None:
        section = CoordinationSectionConfig(max_delegation_rounds=7)
        assert section.to_coordination_config().max_delegation_rounds == 7

    def test_defaults_thread_safe_values(self) -> None:
        run = CoordinationSectionConfig().to_coordination_config()
        assert run.max_delegation_rounds == 3
        assert run.fail_fast is False
