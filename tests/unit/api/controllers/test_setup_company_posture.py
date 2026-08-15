"""Tests for the posture settings seeder in company setup.

A bundle only ever turns a flag on, and every flag names the settings writes
it stands for: the CoS conversational flags and
``cockpit.steering_proposer_enabled`` are one write each, while the
cost-disciplined posture's reasoning depth is four. The seeder writes them
directly rather than mutating the ``meta.self_improvement`` blob.
"""

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

pytestmark = pytest.mark.unit


def _template(posture: PostureName | None) -> CompanyTemplate:
    """Build a minimal template carrying the given posture."""
    return CompanyTemplate(
        metadata=TemplateMetadata(name="t", company_type=CompanyType.CUSTOM),
        agents=(TemplateAgentConfig(role="Developer"),),
        posture=posture,
    )


def _svc() -> SettingsService:
    """A SettingsService double recording every ``set_many`` await."""
    svc: SettingsService = mock_of[SettingsService]()
    return svc


def _set_calls(svc: object) -> dict[tuple[str, str], str]:
    """Collapse the seeder's ``set_many`` batch into ``{(ns, key): value}``."""
    return {
        (namespace, key): value
        for call in svc.set_many.await_args_list  # type: ignore[attr-defined]
        for namespace, key, value in call.args[0]
    }


class TestSeedPostureSettings:
    async def test_no_posture_writes_nothing(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(svc, _template(None))
        assert result is None
        svc.set_many.assert_not_called()  # type: ignore[attr-defined]

    async def test_seeder_only_writes_true(self) -> None:
        """Every recorded write is ``"true"``; postures never downgrade."""
        svc = _svc()
        await seed_posture_settings(
            svc,
            _template(PostureName.SUPERVISED_CLIENT_FACING),
        )
        assert all(value == "true" for value in _set_calls(svc).values())

    async def test_security_hardened_seeds_only_steering(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(
            svc,
            _template(PostureName.SECURITY_HARDENED),
        )
        assert result == "security_hardened"
        calls = _set_calls(svc)
        # Steering is the only settings-resident flag this posture turns on;
        # knowledge_substrate + red_team are config-resident (rendered config).
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        assert ("chief_of_staff", "propose_enabled") not in calls
        assert ("chief_of_staff", "group_chat_enabled") not in calls
        assert ("chief_of_staff", "invite_enabled") not in calls

    async def test_supervised_client_facing_enables_invite(self) -> None:
        svc = _svc()
        await seed_posture_settings(
            svc,
            _template(PostureName.SUPERVISED_CLIENT_FACING),
        )
        calls = _set_calls(svc)
        assert calls[("chief_of_staff", "propose_enabled")] == "true"
        assert calls[("chief_of_staff", "routing_enabled")] == "true"
        assert calls[("chief_of_staff", "group_chat_enabled")] == "true"
        assert calls[("chief_of_staff", "invite_enabled")] == "true"
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"

    async def test_cost_disciplined_dials_reasoning_one_notch_down(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(
            svc,
            _template(PostureName.COST_DISCIPLINED),
        )
        assert result == "cost_disciplined"
        calls = _set_calls(svc)
        # The WHOLE write set, not a sample of it: naming only the keys this
        # posture should write leaves a stale one free to ride along, which is
        # exactly how a retired spend lever would survive its own removal.
        assert set(calls) == {
            ("engine", "reasoning_effort_low"),
            ("engine", "reasoning_effort_normal"),
            ("engine", "reasoning_effort_high"),
            ("engine", "reasoning_effort_critical"),
        }
        assert calls[("engine", "reasoning_effort_low")] == "none"
        assert calls[("engine", "reasoning_effort_normal")] == "none"
        assert calls[("engine", "reasoning_effort_high")] == "low"
        assert calls[("engine", "reasoning_effort_critical")] == "medium"

    async def test_research_autonomous_enables_propose_and_routing(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.RESEARCH_AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls[("chief_of_staff", "propose_enabled")] == "true"
        assert calls[("chief_of_staff", "routing_enabled")] == "true"
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        # No acts-on-your-behalf opt-in for this posture.
        assert ("chief_of_staff", "invite_enabled") not in calls
        assert ("chief_of_staff", "direct_mcp_enabled") not in calls

    async def test_autonomous_enables_steering_not_chat(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        assert ("chief_of_staff", "propose_enabled") not in calls
        assert ("chief_of_staff", "group_chat_enabled") not in calls

    async def test_knowledge_heavy_enables_propose_and_steering(self) -> None:
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.KNOWLEDGE_HEAVY))
        calls = _set_calls(svc)
        assert calls[("cockpit", "steering_proposer_enabled")] == "true"
        assert calls[("chief_of_staff", "propose_enabled")] == "true"
        assert ("chief_of_staff", "group_chat_enabled") not in calls
