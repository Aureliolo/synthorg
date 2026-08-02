"""The adapter keys the directive off the context's own autonomy pair.

Re-deriving the autonomy level or the verbosity tier here rather than reading
what ``build_core_context`` already resolved is how a prompt ends up telling an
agent it is FULL in one section and instructing a SUPERVISED agent in the next,
so the pairing is asserted over the whole matrix.
"""

from datetime import date
from typing import get_args

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import AutonomyDetailLevel
from synthorg.engine.ask_policy.adapter import (
    inject_ask_policy_context,
    should_inject_ask_policy,
)
from synthorg.engine.ask_policy.directives import ASK_DIRECTIVE_LOOKUP
from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.ask_policy.provider import SnapshotAskPolicyProvider
from synthorg.engine.strategy.active_principle import ScopeKind

_TIERS: tuple[AutonomyDetailLevel, ...] = get_args(AutonomyDetailLevel)


def _agent(
    *, role: str = "Developer", department: str = "Engineering"
) -> AgentIdentity:
    return AgentIdentity(
        name="Test Agent",
        role=role,
        department=department,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        personality=PersonalityConfig(description="A precise thinker."),
    )


def _context(*, level: AutonomyLevel, tier: AutonomyDetailLevel) -> dict[str, object]:
    return {"autonomy_mode": level, "autonomy_detail_level": tier}


@pytest.mark.unit
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_injected_text_matches_the_contexts_autonomy_pair(
    tier: AutonomyDetailLevel, level: AutonomyLevel
) -> None:
    context = _context(level=level, tier=tier)
    inject_ask_policy_context(context, _agent(), provider=SnapshotAskPolicyProvider())
    assert context["ask_policy"] is True
    assert context["ask_policy_section"] == ASK_DIRECTIVE_LOOKUP[tier][level]


@pytest.mark.unit
def test_no_provider_disables_the_section() -> None:
    context = _context(level=AutonomyLevel.SEMI, tier="full")
    inject_ask_policy_context(context, _agent(), provider=None)
    assert context["ask_policy"] is False
    assert context["ask_policy_section"] is None


@pytest.mark.unit
def test_disabled_provider_disables_the_section() -> None:
    context = _context(level=AutonomyLevel.SEMI, tier="full")
    inject_ask_policy_context(
        context, _agent(), provider=SnapshotAskPolicyProvider(enabled=False)
    )
    assert context["ask_policy"] is False
    assert context["ask_policy_section"] is None


@pytest.mark.unit
def test_in_scope_extras_render_below_the_base() -> None:
    provider = SnapshotAskPolicyProvider(
        (
            AskDirective(
                id="x_eng",
                text="Engineering: ask before breaking a public API.",
                scope="Engineering",
                scope_kind=ScopeKind.DEPARTMENT,
            ),
            AskDirective(
                id="x_legal",
                text="Lawyer: ask before signing anything.",
                scope="Lawyer",
                scope_kind=ScopeKind.ROLE,
            ),
        )
    )
    context = _context(level=AutonomyLevel.SEMI, tier="full")
    inject_ask_policy_context(context, _agent(), provider=provider)
    section = str(context["ask_policy_section"])
    assert section.startswith(ASK_DIRECTIVE_LOOKUP["full"][AutonomyLevel.SEMI])
    assert "- Engineering: ask before breaking a public API." in section
    assert "Lawyer" not in section


@pytest.mark.unit
def test_should_inject_tracks_the_provider_binding() -> None:
    assert should_inject_ask_policy(provider=None) is False
    assert (
        should_inject_ask_policy(provider=SnapshotAskPolicyProvider(enabled=False))
        is False
    )
    assert should_inject_ask_policy(provider=SnapshotAskPolicyProvider()) is True
