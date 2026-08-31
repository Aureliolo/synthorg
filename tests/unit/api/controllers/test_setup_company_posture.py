"""Tests for the posture settings seeder in company setup.

Every flag names the settings writes it stands for, and a flag is declared
only when its write differs from the setting's registered default: the CoS
conversational flags and ``cockpit.steering_proposer_enabled`` all default
on already, so no posture writes them, while ``agent_invite`` /
``direct_mcp`` default off and the cost-disciplined posture's reasoning
depth is two rows (``high`` and ``critical``; ``low`` and ``normal`` already
sit at the registered floor). The seeder writes them directly rather than
mutating the ``meta.self_improvement`` blob.
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

    async def test_security_hardened_writes_no_settings_resident_flag(self) -> None:
        svc = _svc()
        result = await seed_posture_settings(
            svc,
            _template(PostureName.SECURITY_HARDENED),
        )
        assert result == "security_hardened"
        calls = _set_calls(svc)
        # Steering defaults on already, so this posture's only settings-
        # resident flag has nothing left to write; red_team is
        # config-resident (rendered config).
        assert calls == {}

    async def test_supervised_client_facing_enables_invite(self) -> None:
        svc = _svc()
        await seed_posture_settings(
            svc,
            _template(PostureName.SUPERVISED_CLIENT_FACING),
        )
        calls = _set_calls(svc)
        # propose/routing/group_chat/steering all default on already, so this
        # posture's only real write is the agent-invite opt-in.
        assert calls == {("chief_of_staff", "invite_enabled"): "true"}

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
        # low and normal are omitted on purpose: both already sit at their
        # registered floor ("low"), and a write that only repeats the
        # default is a no-op that pins a row a later default change can
        # never reach.
        assert set(calls) == {
            ("engine", "reasoning_effort_high"),
            ("engine", "reasoning_effort_critical"),
        }
        assert calls[("engine", "reasoning_effort_high")] == "low"
        assert calls[("engine", "reasoning_effort_critical")] == "medium"

    async def test_research_autonomous_writes_no_settings_resident_flag(self) -> None:
        # propose/routing/steering all default on already, and this posture
        # has no acts-on-your-behalf opt-in, so it writes nothing at all.
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.RESEARCH_AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls == {}

    async def test_autonomous_writes_no_settings_resident_flag(self) -> None:
        # steering defaults on already, so this posture writes nothing.
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.AUTONOMOUS))
        calls = _set_calls(svc)
        assert calls == {}

    async def test_knowledge_heavy_writes_no_settings_resident_flag(self) -> None:
        # steering + propose both default on already, so this posture
        # writes nothing.
        svc = _svc()
        await seed_posture_settings(svc, _template(PostureName.KNOWLEDGE_HEAVY))
        calls = _set_calls(svc)
        assert calls == {}
