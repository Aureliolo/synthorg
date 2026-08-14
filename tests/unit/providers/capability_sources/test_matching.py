"""Unit tests for matching source identifiers to configured models."""

import pytest

from synthorg.providers.capability_sources.matching import (
    ConfiguredModelIndex,
    match_identifiers,
    normalise_identifier,
    strip_routing_prefix,
)

pytestmark = pytest.mark.unit


class TestNormalisation:
    def test_case_and_padding_do_not_separate_one_model_from_itself(self) -> None:
        assert normalise_identifier("  Example-Capable-001 ") == "example-capable-001"

    def test_a_routing_prefix_is_dropped_only_once(self) -> None:
        assert strip_routing_prefix("vendor/family/model-y") == "family/model-y"

    def test_an_identifier_without_a_prefix_is_untouched(self) -> None:
        assert strip_routing_prefix("model-y") == "model-y"

    def test_a_trailing_separator_is_not_treated_as_a_prefix(self) -> None:
        assert strip_routing_prefix("model-y/") == "model-y/"


class TestLookup:
    def test_an_exact_identifier_matches(self) -> None:
        index = ConfiguredModelIndex([("provider-a", "example-capable-001")])
        assert index.lookup("example-capable-001") == (
            ("provider-a", "example-capable-001"),
        )

    def test_a_source_prefix_is_stripped_to_reach_a_bare_configured_id(self) -> None:
        index = ConfiguredModelIndex([("provider-a", "model-y")])
        assert index.lookup("vendor/model-y") == (("provider-a", "model-y"),)

    def test_a_configured_prefix_is_stripped_to_reach_a_bare_source_id(self) -> None:
        index = ConfiguredModelIndex([("provider-a", "vendor/model-y")])
        assert index.lookup("model-y") == (("provider-a", "vendor/model-y"),)

    def test_one_model_on_two_providers_both_take_the_evidence(self) -> None:
        """Capability belongs to the model, not to the connection.

        The same model reached through two connections is two different
        calls but one set of abilities.
        """
        index = ConfiguredModelIndex(
            [("provider-a", "model-y"), ("provider-b", "model-y")],
        )
        assert index.lookup("model-y") == (
            ("provider-a", "model-y"),
            ("provider-b", "model-y"),
        )

    def test_an_exact_match_wins_over_a_stripped_one(self) -> None:
        index = ConfiguredModelIndex(
            [("provider-a", "model-y"), ("provider-b", "vendor/model-y")],
        )
        assert index.lookup("model-y") == (("provider-a", "model-y"),)

    def test_an_unknown_identifier_matches_nothing(self) -> None:
        index = ConfiguredModelIndex([("provider-a", "model-y")])
        assert index.lookup("model-z") == ()

    def test_a_variant_suffix_is_never_folded_into_the_base_model(self) -> None:
        """A variant is a different configuration, measured separately.

        Folding it in would average two things the operator chose
        between, and hand one of them a rung it did not earn.
        """
        index = ConfiguredModelIndex([("provider-a", "model-y")])
        assert index.lookup("model-y_high") == ()
        assert index.lookup("model-y-max") == ()

    def test_a_near_miss_is_not_guessed_at(self) -> None:
        index = ConfiguredModelIndex([("provider-a", "model-y")])
        assert index.lookup("model-yy") == ()
        assert index.lookup("model") == ()


class TestReport:
    def test_the_pass_reports_what_it_could_not_place(self) -> None:
        index = ConfiguredModelIndex(
            [("provider-a", "model-y"), ("provider-b", "model-z")],
        )
        resolved, report = match_identifiers(
            index, ["model-y", "model-z", "unheard-of", "also-unheard-of"]
        )
        assert set(resolved) == {"model-y", "model-z"}
        assert report.matched_identifiers == 2
        assert report.unmatched_identifiers == 2
        assert report.matched_models == 2

    def test_one_identifier_covering_two_models_counts_both(self) -> None:
        index = ConfiguredModelIndex(
            [("provider-a", "model-y"), ("provider-b", "model-y")],
        )
        _, report = match_identifiers(index, ["model-y"])
        assert report.matched_identifiers == 1
        assert report.matched_models == 2

    def test_an_empty_roster_places_nothing(self) -> None:
        resolved, report = match_identifiers(ConfiguredModelIndex([]), ["model-y"])
        assert resolved == {}
        assert report.unmatched_identifiers == 1
        assert report.matched_models == 0
