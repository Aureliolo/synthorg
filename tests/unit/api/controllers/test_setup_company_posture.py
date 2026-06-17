"""Tests for the posture settings seeder in company setup."""

import json
from types import SimpleNamespace

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


def _svc() -> SettingsService:
    """A SettingsService double whose ``get`` returns an empty config entry.

    ``load_self_improvement_config`` reads ``entry.value`` (``None`` -> ``{}``),
    so the seeder runs through the happy path rather than the json-decode
    error-swallow fallback.
    """
    svc: SettingsService = mock_of[SettingsService]()
    svc.get.return_value = SimpleNamespace(value=None)  # type: ignore[attr-defined]
    return svc


def _set_calls(svc: object) -> dict[tuple[str, str], str]:
    """Collapse recorded ``set`` awaits into a ``{(namespace, key): value}``."""
    return {
        (call.args[0], call.args[1]): call.args[2]
        for call in svc.set.await_args_list  # type: ignore[attr-defined]
    }


@pytest.mark.unit
class TestSeedPostureSettings:
    async def test_no_posture_writes_nothing(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(svc, _template(None))
        assert result is None
        svc.set.assert_not_called()  # type: ignore[attr-defined]

    async def test_security_hardened_seeds_flags(self) -> None:
        svc = _svc()
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
        # Security posture enables steering but no conversational chat modes.
        cos = meta["chief_of_staff"]
        assert cos["propose_enabled"] is False
        assert cos["group_chat_enabled"] is False
        assert cos["invite_enabled"] is False

    async def test_research_autonomous_enables_chat_and_steering(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.RESEARCH_AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        meta = json.loads(calls[("meta", "self_improvement")])
        assert meta["tool_creation_enabled"] is False
        assert meta["chief_of_staff"]["propose_enabled"] is True

    async def test_supervised_client_facing_enables_chat(self) -> None:
        svc = _svc()
        await seed_posture_settings(
            svc,
            _template(PostureName.SUPERVISED_CLIENT_FACING),
        )
        meta = json.loads(_set_calls(svc)[("meta", "self_improvement")])
        cos = meta["chief_of_staff"]
        assert cos["propose_enabled"] is True
        assert cos["group_chat_enabled"] is True
        assert cos["invite_enabled"] is True

    async def test_cost_disciplined_enables_only_auto_downgrade(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(
            svc,
            _template(PostureName.COST_DISCIPLINED),
        )
        assert result == "cost_disciplined"
        calls = _set_calls(svc)
        assert calls[("budget", "auto_downgrade_enabled")] == "true"
        assert calls[("cockpit", "steering_proposer_enabled")] == "false"
        cos = json.loads(calls[("meta", "self_improvement")])["chief_of_staff"]
        assert cos["propose_enabled"] is False

    async def test_autonomous_enables_steering_not_chat(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        assert calls[("budget", "auto_downgrade_enabled")] == "false"
        cos = json.loads(calls[("meta", "self_improvement")])["chief_of_staff"]
        assert cos["propose_enabled"] is False
        assert cos["group_chat_enabled"] is False

    async def test_knowledge_heavy_enables_propose_and_steering(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.KNOWLEDGE_HEAVY))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        cos = json.loads(calls[("meta", "self_improvement")])["chief_of_staff"]
        assert cos["propose_enabled"] is True
        assert cos["group_chat_enabled"] is False
