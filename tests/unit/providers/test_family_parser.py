"""Tests for the model family/generation parser engine.

The engine is exercised with synthetic ``example-*`` rule tables so no
real vendor/model names appear here; the real per-provider rules live in
``providers/presets.py`` (the only vendor-name-permitted location).
"""

import re
from datetime import date

import pytest

from synthorg.providers.family_parser import (
    FamilyParser,
    FamilyRule,
    ParsedModelIdentity,
    RegexFamilyParser,
    get_family_parser,
)

_EXAMPLE_RULES = {
    "example-provider": (
        FamilyRule(
            capture=re.compile(
                r"^(?P<family>example-(?:large|small))-(?P<gen>\d+(?:-\d+)?)$",
            ),
            family_template="{family}",
        ),
    ),
    "example-sized": (
        FamilyRule(
            capture=re.compile(
                r"^example-(?P<gen>\d+(?:\.\d+)?)(?P<variant>-mini|-nano)?$",
            ),
            family_template="example{variant}",
        ),
    ),
}


@pytest.mark.unit
class TestRegexFamilyParser:
    def test_parses_family_and_generation(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse("example-large-4-5", litellm_provider="example-provider")
        assert parsed.family == "example-large"
        assert parsed.generation == 4.5
        assert parsed.release_date is None

    def test_parses_dated_variant(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse(
            "example-small-2-20250514",
            litellm_provider="example-provider",
        )
        assert parsed.family == "example-small"
        assert parsed.generation == 2.0
        assert parsed.release_date == date(2025, 5, 14)

    def test_variant_folds_into_family(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        base = parser.parse("example-4.1", litellm_provider="example-sized")
        mini = parser.parse("example-4.1-mini", litellm_provider="example-sized")
        assert base.family == "example"
        assert mini.family == "example-mini"
        assert base.generation == mini.generation == 4.1

    def test_unknown_provider_uses_generic_fallback(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse("examplellm3.3-coder", litellm_provider="not-in-table")
        assert parsed.family == "examplellm"
        assert parsed.generation == 3.3

    def test_generic_strips_date_suffix(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse("examplellm3.3-20250101", litellm_provider=None)
        assert parsed.family == "examplellm"
        assert parsed.generation == 3.3
        assert parsed.release_date == date(2025, 1, 1)

    def test_textual_only_id_has_family_no_generation(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse("modelnoversion", litellm_provider=None)
        assert parsed.family == "modelnoversion"
        assert parsed.generation is None

    def test_unparseable_id_yields_empty_identity(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        parsed = parser.parse("12345", litellm_provider=None)
        assert parsed.family is None
        assert parsed.generation is None

    def test_provider_rule_miss_falls_through_to_generic(self) -> None:
        parser = RegexFamilyParser(_EXAMPLE_RULES)
        # Provider has a rule table, but this id does not match it.
        parsed = parser.parse("other2.0", litellm_provider="example-provider")
        assert parsed.family == "other"
        assert parsed.generation == 2.0


@pytest.mark.unit
class TestFactory:
    def test_get_family_parser_is_singleton(self) -> None:
        assert get_family_parser() is get_family_parser()

    def test_default_parser_satisfies_protocol(self) -> None:
        assert isinstance(get_family_parser(), FamilyParser)

    def test_default_parser_returns_identity(self) -> None:
        parsed = get_family_parser().parse(
            "example-large-001",
            litellm_provider="example-provider-x",
        )
        assert isinstance(parsed, ParsedModelIdentity)
