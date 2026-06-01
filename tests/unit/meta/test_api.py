"""Unit tests for meta-loop API controller."""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.meta import (
    ConversationalProposeRequest,
    MetaController,
)
from synthorg.api.rate_limits.policies import RATE_LIMIT_POLICIES
from synthorg.core.types import NotBlankStr
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

    def test_has_chat_propose_endpoint(self) -> None:
        methods = [name for name in dir(MetaController) if not name.startswith("_")]
        assert "chat_propose" in methods

    def test_propose_rate_limit_policy_registered(self) -> None:
        assert "meta.chat.propose" in RATE_LIMIT_POLICIES


class TestConversationalProposeRequest:
    """Request-model validation at the propose boundary."""

    def test_minimal_valid(self) -> None:
        req = ConversationalProposeRequest(message=NotBlankStr("do X"))
        assert req.conversation_id is None
        assert req.project is None

    def test_blank_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConversationalProposeRequest(message=NotBlankStr("   "))

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ConversationalProposeRequest(
                message=NotBlankStr("hi"),
                unexpected="x",  # type: ignore[call-arg]
            )

    def test_message_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ConversationalProposeRequest(
                message=NotBlankStr("a" * 2001),
            )


class TestMetaConfigDefaults:
    """Test that default config matches expectations."""

    def test_default_config_disabled(self) -> None:
        cfg = SelfImprovementConfig()
        assert cfg.enabled is False

    def test_default_has_10_rules(self) -> None:
        rules = default_rules()
        assert len(rules) == 10

    def test_default_has_9_mcp_tools(self) -> None:
        assert len(SIGNAL_TOOLS) == 9
