"""Unit tests for the shared agent-persona system-prompt renderer.

Guards the Phase-0 extraction of ``_render_system_prompt`` out of the
meeting agent caller: the shared renderer must produce byte-identical
output to the meeting caller's wrapper, and must always carry the
untrusted-content directive.
"""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.communication.meeting.agent_caller import _render_system_prompt
from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import AgentStatus, SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    untrusted_content_directive,
)

pytestmark = pytest.mark.unit

_DEFAULT_TRAITS: tuple[NotBlankStr, ...] = (NotBlankStr("analytical"),)
_DEFAULT_STYLE: NotBlankStr = NotBlankStr("concise")


def _identity(
    *,
    traits: tuple[NotBlankStr, ...] = _DEFAULT_TRAITS,
    communication_style: NotBlankStr = _DEFAULT_STYLE,
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr("Casey"),
        role=NotBlankStr("CFO"),
        department=NotBlankStr("executive"),
        level=SeniorityLevel.C_SUITE,
        personality=PersonalityConfig(
            traits=traits,
            communication_style=communication_style,
        ),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-medium-001"),
            temperature=0.7,
            max_tokens=4096,
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


class TestRenderAgentSystemPrompt:
    def test_includes_identity_preamble(self) -> None:
        prompt = render_agent_system_prompt(_identity())
        assert "You are Casey, a CFO in the executive department." in prompt
        assert "Seniority level: c_suite." in prompt
        assert "Personality traits: analytical." in prompt
        assert "Communication style: concise." in prompt

    def test_carries_default_untrusted_directive(self) -> None:
        prompt = render_agent_system_prompt(_identity())
        expected = untrusted_content_directive((TAG_TASK_DATA, TAG_PEER_CONTRIBUTION))
        assert expected in prompt

    def test_omits_traits_line_when_empty(self) -> None:
        prompt = render_agent_system_prompt(_identity(traits=()))
        assert "Personality traits" not in prompt

    def test_custom_fences_emit_only_those_tags(self) -> None:
        prompt = render_agent_system_prompt(
            _identity(),
            fences=(TAG_TASK_DATA,),
        )
        expected = untrusted_content_directive((TAG_TASK_DATA,))
        assert expected in prompt

    def test_meeting_caller_wrapper_matches_shared_renderer(self) -> None:
        # The Phase-0 extraction must not change meeting behaviour: the
        # caller's private wrapper delegates to the shared renderer.
        identity = _identity()
        assert _render_system_prompt(identity) == render_agent_system_prompt(identity)
