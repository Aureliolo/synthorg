"""Tests for deriving a conversation's title from its opening message.

The derivation only ever trims what a person typed: no summarisation and no
model, so every assertion here is about which characters survive.
"""

import pytest

from synthorg.meta.chief_of_staff.conversation_title import (
    _MAX_TITLE_CHARS,
    derive_conversation_title,
)

pytestmark = pytest.mark.unit

_ELLIPSIS = "…"


class TestShortMessages:
    def test_a_short_message_is_its_own_title(self) -> None:
        assert derive_conversation_title("Build me a Tetris clone") == (
            "Build me a Tetris clone"
        )

    def test_surrounding_whitespace_goes(self) -> None:
        assert derive_conversation_title("  Build me a clone \n") == (
            "Build me a clone"
        )

    def test_internal_whitespace_collapses_to_one_line(self) -> None:
        # A drawer row is one line, so a multi-line message must not render
        # as a title with a newline in the middle of it.
        assert derive_conversation_title("Build me\n\na clone") == "Build me a clone"

    def test_a_heading_marker_is_structure_not_words(self) -> None:
        assert derive_conversation_title("## Build me a clone") == "Build me a clone"

    def test_a_bullet_marker_is_structure_too(self) -> None:
        assert derive_conversation_title("- Build me a clone") == "Build me a clone"


class TestNothingToNameItBy:
    @pytest.mark.parametrize("content", ["", "   ", "\n\t ", "###", "> "])
    def test_content_that_reduces_to_nothing_has_no_title(self, content: str) -> None:
        # None rather than a placeholder: the caller already renders what kind
        # of conversation this is, and inventing a title takes that away.
        assert derive_conversation_title(content) is None


class TestLongMessages:
    def test_a_long_message_is_cut_on_a_word_boundary(self) -> None:
        content = (
            "Build me a dashboard that shows every agent and what it is "
            "doing right now, with filters"
        )
        title = derive_conversation_title(content)
        assert title is not None
        assert title.endswith(_ELLIPSIS)
        assert content.startswith(title[: -len(_ELLIPSIS)])
        assert not title[: -len(_ELLIPSIS)].endswith(" ")
        # Cut on a boundary, so the last rendered word is a whole word.
        assert title[: -len(_ELLIPSIS)].split()[-1] in content.split()

    def test_a_single_enormous_word_is_trimmed_hard(self) -> None:
        # No boundary exists inside the budget, so there is nothing to cut on;
        # rendering it whole would break the row instead.
        title = derive_conversation_title("x" * 500)
        assert title is not None
        assert title.endswith(_ELLIPSIS)
        # The marker's own length comes off the budget: a cap the marker
        # pushes past is not a cap on what the row renders.
        assert len(title) <= _MAX_TITLE_CHARS

    @pytest.mark.parametrize("length", [81, 120, 500])
    def test_no_title_is_ever_longer_than_the_ceiling(self, length: int) -> None:
        title = derive_conversation_title(" ".join("word" for _ in range(length)))
        assert title is not None
        assert len(title) <= _MAX_TITLE_CHARS

    def test_a_message_exactly_at_the_ceiling_is_not_cut(self) -> None:
        content = "a" * 80
        assert derive_conversation_title(content) == content
