"""Tests for the posture settings seeder in company setup."""

import json

import pytest

from synthorg.api.controllers.setup._posture_seeding import seed_posture_settings
from synthorg.organization.enums import CompanyType
from synthorg.settings.service import SettingsService
from synthorg.templates.enums import PostureName
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
)
from tests._shared import mock_of


def _template(posture: PostureName | None) -> CompanyTemplate:
    """Build a minimal template carrying the given posture."""
    return CompanyTemplate(
        metadata=TemplateMetadata(name="t", company_type=CompanyType.CUSTOM),
        agents=(TemplateAgentConfig(role="Developer"),),
        posture=posture,
    )


def _set_calls(svc: object) -> dict[tuple[str, str], str]:
    """Collapse recorded ``set`` awaits into a ``{(namespace, key): value}``."""
    return {
        (call.args[0], call.args[1]): call.args[2]
        for call in svc.set.await_args_list  # type: ignore[attr-defined]
    }


@pytest.mark.unit
class TestSeedPostureSettings:
    async def test_no_posture_writes_nothing(self) -> None:
        svc = mock_of[SettingsService]()
        result = await seed_posture_settings(svc, _template(None))
        assert result is None
        svc.set.assert_not_called()

    async def test_security_hardened_seeds_flags(self) -> None:
        svc = mock_of[SettingsService]()
        result = await seed_posture_settings(
            svc,
            _template(PostureName.SECURITY_HARDENED),
        )
        assert result == "security_hardened"
        calls = _set_calls(svc)
        # Steering on; auto-downgrade off for this posture.
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        assert calls[("budget", "auto_downgrade_enabled")] == "false"
        meta = json.loads(calls[("meta", "self_improvement")])
        # Toolsmith stays an operator opt-in; postures never flip it.
        assert meta["tool_creation_enabled"] is False

    async def test_research_autonomous_enables_chat_and_steering(self) -> None:
        svc = mock_of[SettingsService]()
        await seed_posture_settings(svc, _template(PostureName.RESEARCH_AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        meta = json.loads(calls[("meta", "self_improvement")])
        assert meta["tool_creation_enabled"] is False
        assert meta["chief_of_staff"]["propose_enabled"] is True

    async def test_supervised_client_facing_enables_chat(self) -> None:
        svc = mock_of[SettingsService]()
        await seed_posture_settings(
            svc,
            _template(PostureName.SUPERVISED_CLIENT_FACING),
        )
        meta = json.loads(_set_calls(svc)[("meta", "self_improvement")])
        cos = meta["chief_of_staff"]
        assert cos["propose_enabled"] is True
        assert cos["group_chat_enabled"] is True
        assert cos["invite_enabled"] is True
