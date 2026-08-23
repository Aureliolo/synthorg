"""Unit tests for meta-loop API controller."""

import pytest

from synthorg.api.controllers.meta import MetaController
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.mcp.tools import SIGNAL_TOOLS
from synthorg.meta.rules.builtin import default_rules

pytestmark = pytest.mark.unit


class TestMetaControllerRoutes:
    """Verify MetaController route definitions."""

    def test_controller_path(self) -> None:
        assert MetaController.path == "/meta"

    def test_has_config_endpoint(self) -> None:
        methods = [name for name in dir(MetaController) if not name.startswith("_")]
        assert "get_config" in methods

    def test_has_rules_endpoint(self) -> None:
        methods = [name for name in dir(MetaController) if not name.startswith("_")]
        assert "list_rules" in methods

    def test_has_cycle_endpoint(self) -> None:
        methods = [name for name in dir(MetaController) if not name.startswith("_")]
        assert "trigger_cycle" in methods


class TestMetaConfigDefaults:
    """Test that default config matches expectations."""

    def test_default_config_disabled(self) -> None:
        cfg = SelfImprovementConfig()
        assert cfg.enabled is False

    def test_default_has_9_rules(self) -> None:
        rules = default_rules()
        assert len(rules) == 9

    def test_default_has_8_mcp_tools(self) -> None:
        assert len(SIGNAL_TOOLS) == 8
