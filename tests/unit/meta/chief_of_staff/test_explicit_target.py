# module-kind: tests
"""Unit tests for @role explicit-target parsing."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._explicit_target import extract_explicit_targets

pytestmark = pytest.mark.unit


class TestExtractExplicitTargets:
    def test_no_mentions_returns_message_unchanged(self) -> None:
        body, targets = extract_explicit_targets("what is our runway?")
        assert body == "what is our runway?"
        assert targets == ()

    def test_single_leading_mention(self) -> None:
        body, targets = extract_explicit_targets("@CFO what is our runway?")
        assert body == "what is our runway?"
        assert targets == (NotBlankStr("CFO"),)

    def test_multiple_leading_mentions(self) -> None:
        body, targets = extract_explicit_targets("@CFO @cto discuss the budget")
        assert body == "discuss the budget"
        assert targets == (NotBlankStr("CFO"), NotBlankStr("cto"))

    def test_mention_only_keeps_original_as_body(self) -> None:
        # A message that is nothing but a mention must not become empty.
        body, targets = extract_explicit_targets("@cfo")
        assert body == "@cfo"
        assert targets == (NotBlankStr("cfo"),)

    def test_mid_sentence_mention_is_not_a_target(self) -> None:
        body, targets = extract_explicit_targets("ask the @cfo about this")
        assert body == "ask the @cfo about this"
        assert targets == ()

    def test_hyphenated_and_dotted_names(self) -> None:
        body, targets = extract_explicit_targets("@ada.lovelace @q-a review")
        assert body == "review"
        assert targets == (NotBlankStr("ada.lovelace"), NotBlankStr("q-a"))
