"""Auto-name generation and the locale set it draws from.

Both are reached on the ordinary render path: ``_agent_expansion`` names any
agent whose template left ``name`` blank or unrendered, and the setup
controller's randomise endpoint calls the same generator. A rendered company
reads as people rather than as ``Agent 1`` ... ``Agent N`` because of this.
"""

import pytest

from synthorg.templates.agent_naming import _MAX_NAME_PART_LEN, generate_auto_name
from synthorg.templates.locales import (
    ALL_LATIN_LOCALES,
    LOCALE_DISPLAY_NAMES,
    LOCALE_REGIONS,
    resolve_locales,
)

pytestmark = pytest.mark.unit

#: Enough seeds to show the locale is drawn per call rather than fixed, without
#: paying for a Faker instance per Latin locale.
_DIVERSITY_SEEDS = 10


class TestGenerateAutoName:
    def test_returns_a_nonempty_string(self) -> None:
        name = generate_auto_name(seed=0)

        assert isinstance(name, str)
        assert name

    def test_a_seed_makes_it_deterministic(self) -> None:
        """The expansion path seeds on the agent's index.

        Two renders of one template must therefore produce the same roster, or
        a re-render silently renames everybody.
        """
        assert generate_auto_name(seed=42) == generate_auto_name(seed=42)

    def test_different_seeds_give_diverse_names(self) -> None:
        names = {generate_auto_name(seed=i) for i in range(_DIVERSITY_SEEDS)}

        assert len(names) >= 2

    def test_a_single_locale_is_accepted(self) -> None:
        name = generate_auto_name(seed=42, locales=["en_US"])

        assert isinstance(name, str)
        assert name

    def test_several_locales_are_accepted(self) -> None:
        name = generate_auto_name(seed=42, locales=["en_US", "fr_FR", "de_DE"])

        assert isinstance(name, str)
        assert name

    def test_it_works_with_no_seed_at_all(self) -> None:
        """The randomise endpoint passes no seed, so this is its whole path."""
        name = generate_auto_name()

        assert isinstance(name, str)
        assert name

    def test_the_name_is_two_capped_parts(self) -> None:
        """Agents display as ``First Last``, and the UI has a width.

        Faker returns compound and hyphenated parts for some locales, so both
        halves are retried and then truncated rather than passed through.
        """
        parts = generate_auto_name(seed=7).split()

        assert len(parts) == 2
        assert all(len(part) <= _MAX_NAME_PART_LEN for part in parts)
        assert all("-" not in part for part in parts)


class TestTheLocaleSet:
    def test_it_holds_the_latin_script_locales(self) -> None:
        assert len(ALL_LATIN_LOCALES) >= 50

    def test_every_locale_belongs_to_a_region(self) -> None:
        """The dashboard groups the picker by region, so a gap is invisible."""
        grouped = {loc for locs in LOCALE_REGIONS.values() for loc in locs}

        assert grouped == set(ALL_LATIN_LOCALES)

    def test_every_locale_has_a_display_name(self) -> None:
        for locale in ALL_LATIN_LOCALES:
            assert locale in LOCALE_DISPLAY_NAMES

    def test_no_locale_faker_has_deprecated(self) -> None:
        """``fr_QC`` is retired upstream; constructing it warns and then fails."""
        assert "fr_QC" not in ALL_LATIN_LOCALES


class TestResolveLocales:
    def test_the_all_sentinel_expands_to_everything(self) -> None:
        assert resolve_locales(["__all__"]) == list(ALL_LATIN_LOCALES)

    def test_none_expands_to_everything(self) -> None:
        assert resolve_locales(None) == list(ALL_LATIN_LOCALES)

    def test_an_empty_list_expands_to_everything(self) -> None:
        """Empty is falsy, so an operator who cleared the picker gets them all."""
        assert resolve_locales([]) == list(ALL_LATIN_LOCALES)

    def test_a_specific_selection_is_kept_in_order(self) -> None:
        assert resolve_locales(["en_US", "fr_FR"]) == ["en_US", "fr_FR"]

    def test_an_unknown_code_is_filtered_out(self) -> None:
        """A persisted setting can name a locale a Faker upgrade retired."""
        assert resolve_locales(["en_US", "invalid_XX", "fr_FR"]) == ["en_US", "fr_FR"]

    def test_an_all_unknown_selection_resolves_to_nothing(self) -> None:
        """Empty, not everything: the generator's own fallback covers this.

        Answering the full set here would silently widen a selection the
        operator narrowed, which is the opposite of what they asked for.
        """
        assert resolve_locales(["invalid_XX", "bogus_YY"]) == []
