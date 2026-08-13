"""The ask directive reaches a real rendered system prompt at every level."""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import AutonomyDetailLevel, CapabilityLevel
from synthorg.engine.ask_policy.directives import ASK_DIRECTIVE_LOOKUP
from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.ask_policy.provider import (
    SnapshotAskPolicyProvider,
    set_ask_policy_provider,
)
from synthorg.engine.prompt import build_system_prompt
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    untrusted_content_directive,
)
from synthorg.engine.strategy.active_principle import ScopeKind
from tests.unit.engine.ask_policy.conftest import agent

_SECTION_HEADING = "## Asking Rather Than Guessing"

#: The prompt profile for each model tier picks the verbosity tier, so driving
#: ``model_tier`` is how the three tiers are exercised end to end.
_TIER_BY_MODEL: tuple[tuple[CapabilityLevel, AutonomyDetailLevel], ...] = (
    ("large", "full"),
    ("medium", "summary"),
    ("small", "minimal"),
)


def _autonomy(level: AutonomyLevel) -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=level,
        auto_approve_actions=frozenset(),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


@pytest.mark.unit
def test_prompt_includes_the_section_when_bound() -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider())
    result = build_system_prompt(agent=agent())
    assert _SECTION_HEADING in result.content
    assert "ask_policy" in result.sections


@pytest.mark.unit
def test_prompt_omits_the_section_without_a_provider() -> None:
    set_ask_policy_provider(None)
    result = build_system_prompt(agent=agent())
    assert _SECTION_HEADING not in result.content
    assert "ask_policy" not in result.sections


@pytest.mark.unit
def test_prompt_omits_the_section_when_disabled() -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider(enabled=False))
    result = build_system_prompt(agent=agent())
    assert _SECTION_HEADING not in result.content
    assert "ask_policy" not in result.sections


@pytest.mark.unit
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_directive_present_at_every_autonomy_level(level: AutonomyLevel) -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider())
    result = build_system_prompt(agent=agent(), effective_autonomy=_autonomy(level))
    assert ASK_DIRECTIVE_LOOKUP["full"][level] in result.content


@pytest.mark.unit
@pytest.mark.parametrize(("model_tier", "tier"), _TIER_BY_MODEL)
def test_directive_present_at_every_verbosity_tier(
    model_tier: CapabilityLevel, tier: AutonomyDetailLevel
) -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider())
    result = build_system_prompt(
        agent=agent(),
        effective_autonomy=_autonomy(AutonomyLevel.SEMI),
        model_tier=model_tier,
    )
    assert ASK_DIRECTIVE_LOOKUP[tier][AutonomyLevel.SEMI] in result.content


@pytest.mark.unit
def test_defaults_to_semi_without_resolved_autonomy() -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider())
    result = build_system_prompt(agent=agent())
    assert ASK_DIRECTIVE_LOOKUP["full"][AutonomyLevel.SEMI] in result.content


@pytest.mark.unit
def test_operator_extras_render_after_the_standing_directive() -> None:
    set_ask_policy_provider(
        SnapshotAskPolicyProvider(
            (
                AskDirective(
                    id="x_eng",
                    text="Engineering: ask before breaking a public API.",
                    scope="Engineering",
                    scope_kind=ScopeKind.DEPARTMENT,
                ),
            )
        )
    )
    result = build_system_prompt(agent=agent())
    standing = ASK_DIRECTIVE_LOOKUP["full"][AutonomyLevel.SEMI]
    assert result.content.index(standing) < result.content.index(
        "Engineering: ask before breaking a public API."
    )
    # The extra is fenced, and the untrusted-content directive names that
    # fence: a fence the directive never mentions teaches the model nothing.
    assert f"<{TAG_CONFIG_VALUE}>" in result.content
    assert untrusted_content_directive((TAG_CONFIG_VALUE,)) in result.content


@pytest.mark.unit
def test_section_follows_autonomy_in_the_rendered_prompt() -> None:
    set_ask_policy_provider(SnapshotAskPolicyProvider())
    result = build_system_prompt(agent=agent())
    assert result.content.index("## Autonomy") < result.content.index(_SECTION_HEADING)
    assert result.sections.index("autonomy") < result.sections.index("ask_policy")
