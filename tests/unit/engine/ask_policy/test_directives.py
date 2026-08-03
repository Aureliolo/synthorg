"""The standing ask directive is present, and says the same thing, everywhere.

The whole point of the subsystem is that an agent is told to ask rather than
guess at *every* autonomy level and *every* verbosity tier. A missing cell is an
autonomy level at which the organisation silently stops asking, so totality and
substance are asserted over the full matrix rather than spot-checked.
"""

from typing import get_args

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import AutonomyDetailLevel
from synthorg.engine.ask_policy.directives import (
    ASK_DIRECTIVE_LOOKUP,
    ASK_DIRECTIVES,
    ASK_DIRECTIVES_MINIMAL,
    ASK_DIRECTIVES_SUMMARY,
    base_directive,
)

_TIERS: tuple[AutonomyDetailLevel, ...] = get_args(AutonomyDetailLevel)

#: A directive earns its place only by naming the trigger. "Ask" alone would
#: pass on prose telling an agent to ask about everything.
_HARD_TO_REVERSE_FORMS = ("hard to reverse", "hard-to-reverse", "irreversible")


@pytest.mark.unit
@pytest.mark.parametrize("tier", _TIERS)
def test_every_autonomy_level_present_at_every_tier(tier: AutonomyDetailLevel) -> None:
    assert set(ASK_DIRECTIVE_LOOKUP[tier]) == set(AutonomyLevel)


@pytest.mark.unit
def test_lookup_covers_every_detail_level() -> None:
    assert set(ASK_DIRECTIVE_LOOKUP) == set(_TIERS)


@pytest.mark.unit
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_directive_says_ask_on_a_material_irreversible_choice(
    tier: AutonomyDetailLevel, level: AutonomyLevel
) -> None:
    text = ASK_DIRECTIVE_LOOKUP[tier][level].casefold()
    assert text.strip()
    assert "ask" in text
    assert "material" in text
    assert any(form in text for form in _HARD_TO_REVERSE_FORMS)


@pytest.mark.unit
@pytest.mark.parametrize("tier", _TIERS)
def test_each_level_has_distinct_text(tier: AutonomyDetailLevel) -> None:
    texts = list(ASK_DIRECTIVE_LOOKUP[tier].values())
    assert len(set(texts)) == len(texts)


@pytest.mark.unit
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_tiers_shorten_monotonically(level: AutonomyLevel) -> None:
    assert len(ASK_DIRECTIVES_MINIMAL[level]) <= len(ASK_DIRECTIVES_SUMMARY[level])
    assert len(ASK_DIRECTIVES_SUMMARY[level]) <= len(ASK_DIRECTIVES[level])


@pytest.mark.unit
def test_no_directive_contains_an_em_dash() -> None:
    em_dash = chr(0x2014)
    for tier in _TIERS:
        for text in ASK_DIRECTIVE_LOOKUP[tier].values():
            assert em_dash not in text


@pytest.mark.unit
@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("level", list(AutonomyLevel))
def test_base_directive_is_total(
    tier: AutonomyDetailLevel, level: AutonomyLevel
) -> None:
    assert (
        base_directive(autonomy=level, detail=tier) == ASK_DIRECTIVE_LOOKUP[tier][level]
    )
