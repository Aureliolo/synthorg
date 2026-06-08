"""Tests for epistemic marker detection in compaction summaries."""

import pytest

from synthorg.core.task_enums import Complexity
from synthorg.engine.compaction.epistemic import (
    count_epistemic_markers,
    extract_marker_sentences,
    should_preserve_message,
)


@pytest.mark.unit
class TestCountEpistemicMarkers:
    """Tests for count_epistemic_markers function."""

    def test_no_markers(self) -> None:
        """Text with no markers returns 0."""
        text = "The sky is blue and the grass is green."
        assert count_epistemic_markers(text) == 0

    def test_one_marker_from_each_group(self) -> None:
        """Count distinct pattern matches across all 5 groups."""
        # One hedging only: "hmm"
        assert count_epistemic_markers("Hmm, that's odd.") == 1

        # One reconsideration only: "on second thought"
        assert count_epistemic_markers("On second thought, no.") == 1

        # One uncertainty only: "perhaps"
        assert count_epistemic_markers("Perhaps we should try.") == 1

        # One verification only: "verify"
        assert count_epistemic_markers("Let me verify this.") == 1

        # One correction only: "hold on"
        assert count_epistemic_markers("Hold on, that's wrong.") == 1

    def test_multiple_from_same_group_counted_once(self) -> None:
        """Multiple matches from same pattern group counted as 1."""
        text = "Wait, hmm, Hmm again. These are all hedging."
        # All three are in the hedging group, so count is 1
        assert count_epistemic_markers(text) == 1

    def test_mixed_markers(self) -> None:
        """Multiple distinct patterns counted correctly."""
        text = "Wait, actually I was wrong. Let me verify this."
        # "wait" (hedging), "actually" (reconsideration), "verify" (verification)
        assert count_epistemic_markers(text) == 3

    def test_case_insensitive_matching(self) -> None:
        """Markers match regardless of case."""
        text1 = "Hmm, that looks odd."
        text2 = "HMM, THAT LOOKS ODD."
        text3 = "hMm, ThAt LoOkS oDd."
        assert count_epistemic_markers(text1) == count_epistemic_markers(text2)
        assert count_epistemic_markers(text2) == count_epistemic_markers(text3)
        assert count_epistemic_markers(text1) == 1


@pytest.mark.unit
class TestShouldPreserveMessage:
    """Tests for should_preserve_message function."""

    def test_complex_with_one_marker_preserves(self) -> None:
        """COMPLEX complexity: preserve if >= 1 marker."""
        text = "Wait, I need to think about this more."
        assert should_preserve_message(text, Complexity.COMPLEX) is True

    def test_epic_with_one_marker_preserves(self) -> None:
        """EPIC complexity: preserve if >= 1 marker."""
        text = "Hmm, let me reconsider this."
        assert should_preserve_message(text, Complexity.EPIC) is True

    def test_simple_with_one_marker_does_not_preserve(self) -> None:
        """SIMPLE complexity: preserve only if >= 3 markers."""
        text = "Wait, there's an issue."
        assert should_preserve_message(text, Complexity.SIMPLE) is False

    def test_simple_with_three_markers_preserves(self) -> None:
        """SIMPLE complexity: preserve if >= 3 markers."""
        text = "Wait, actually, let me verify this carefully."
        # "wait" (hedging), "actually" (reconsideration), "verify" (verification)
        assert should_preserve_message(text, Complexity.SIMPLE) is True

    def test_medium_with_two_markers_does_not_preserve(self) -> None:
        """MEDIUM complexity: preserve only if >= 3 markers."""
        # "hmm" (hedging) + "perhaps" (uncertainty) = 2 groups
        text = "Hmm, perhaps we should try a different approach."
        assert should_preserve_message(text, Complexity.MEDIUM) is False

    def test_medium_with_three_markers_preserves(self) -> None:
        """MEDIUM complexity: preserve if >= 3 markers."""
        text = "Wait, actually I was wrong. Let me verify."
        # "wait" (hedging), "actually" (reconsideration), "verify" (verification)
        assert should_preserve_message(text, Complexity.MEDIUM) is True

    def test_no_markers_never_preserves(self) -> None:
        """Text with no markers never preserves."""
        text = "This is a straightforward statement with no reasoning markers."
        assert should_preserve_message(text, Complexity.COMPLEX) is False
        assert should_preserve_message(text, Complexity.EPIC) is False
        assert should_preserve_message(text, Complexity.SIMPLE) is False
        assert should_preserve_message(text, Complexity.MEDIUM) is False


@pytest.mark.unit
class TestExtractMarkerSentences:
    """Tests for extract_marker_sentences function."""

    def test_no_markers_returns_empty_string(self) -> None:
        """Text with no marker sentences returns empty string."""
        text = "This is a straightforward statement. No reasoning markers here."
        result = extract_marker_sentences(text)
        assert result == ""

    def test_extract_single_marker_sentence(self) -> None:
        """Extract single sentence containing a marker."""
        text = (
            "The plan seems straightforward. "
            "Wait, I just realized something. "
            "Let me move forward."
        )
        result = extract_marker_sentences(text)
        assert "Wait, I just realized something" in result

    def test_extract_multiple_marker_sentences(self) -> None:
        """Extract multiple sentences with markers and join with '; '."""
        text = (
            "First statement. "
            "Wait, let me reconsider. "
            "Some filler text here. "
            "Actually, I was wrong. "
            "More filler. "
            "But hold on, there's another issue."
        )
        result = extract_marker_sentences(text)
        assert "Wait, let me reconsider" in result
        assert "Actually, I was wrong" in result
        assert "But hold on, there's another issue" in result
        # Check they're joined with "; "
        assert "; " in result

    def test_truncates_at_max_chars(self) -> None:
        """First marker sentence exceeding max_chars is truncated."""
        # Single long marker sentence that exceeds max_chars
        long_marker = "Wait, " + "x" * 100 + " important"
        text = long_marker
        max_chars = 30
        result = extract_marker_sentences(text, max_chars=max_chars)

        # Should be truncated to exactly max_chars (first-sentence path)
        assert len(result) == max_chars
        assert result == long_marker[:max_chars]

    def test_truncates_multi_sentence_at_max_chars(self) -> None:
        """Multiple short marker sentences stop accumulating at max_chars."""
        sentences = [
            "Wait, I need to think about something.",
            "Actually, let me reconsider this completely.",
            "Hmm, this is more complex than I thought.",
            "Perhaps we should verify each step carefully.",
        ]
        text = " ".join(sentences)
        max_chars = 50
        result = extract_marker_sentences(text, max_chars=max_chars)

        # Should contain at most max_chars worth of content
        assert len(result) <= max_chars

    def test_respects_max_chars_default(self) -> None:
        """Default max_chars is 200 -- single long sentence is truncated."""
        text = "Wait, " + "x" * 300
        result = extract_marker_sentences(text)
        # Single sentence exceeds default 200 chars -> truncated to exactly 200
        assert len(result) == 200

    def test_handles_newlines_as_sentence_boundaries(self) -> None:
        """Newlines are treated as sentence boundaries."""
        text = "Normal statement.\nWait, something important here.\nMore text."
        result = extract_marker_sentences(text)
        assert "Wait, something important here" in result
        assert "Normal statement" not in result

    def test_handles_multiple_punctuation_marks(self) -> None:
        """Handles sentences ending with ?, !, or periods."""
        text = "Really? Actually, wait! Let me check. Hmm, this needs verification?"
        result = extract_marker_sentences(text)
        assert "Actually, wait" in result or "Actually" in result

    def test_strips_whitespace_from_sentences(self) -> None:
        """Extracted sentences have whitespace stripped."""
        text = "   Normal.      Wait, important.      More.   "
        result = extract_marker_sentences(text)
        # Should not have extra whitespace
        assert "Wait, important" in result
        assert not result.startswith(" ")
        assert not result.endswith(" ")
        assert "  " not in result
