"""Unit tests for the deterministic prose/code segmenter."""

import pytest

from synthorg.engine.output_style.models import OutputChannel, SegmentKind
from synthorg.engine.output_style.segmenter import segment


def _covers(text: str, channel: OutputChannel) -> bool:
    """Whether segments contiguously and exactly reconstruct the input."""
    segs = segment(text, channel)
    rebuilt = "".join(s.text for s in segs)
    contiguous = all(segs[i].end == segs[i + 1].start for i in range(len(segs) - 1))
    return rebuilt == text and contiguous


class TestSegmenter:
    @pytest.mark.unit
    def test_empty(self) -> None:
        assert segment("", OutputChannel.DELIVERABLE) == ()

    @pytest.mark.unit
    def test_code_channel_is_single_code_span(self) -> None:
        segs = segment("hello world", OutputChannel.CODE_FILE)
        assert len(segs) == 1
        assert segs[0].kind is SegmentKind.CODE

    @pytest.mark.unit
    def test_commit_message_is_code(self) -> None:
        segs = segment("fix: thing", OutputChannel.COMMIT_MESSAGE)
        assert all(s.kind is SegmentKind.CODE for s in segs)

    @pytest.mark.unit
    def test_prose_channel_plain_text_is_prose(self) -> None:
        segs = segment("just some prose", OutputChannel.DELIVERABLE)
        assert all(s.kind is SegmentKind.PROSE for s in segs)

    @pytest.mark.unit
    def test_fenced_block_is_code(self) -> None:
        text = "before\n```python\nx = 1\n```\nafter\n"
        segs = segment(text, OutputChannel.DELIVERABLE)
        code_text = "".join(s.text for s in segs if s.kind is SegmentKind.CODE)
        assert "x = 1" in code_text
        prose_text = "".join(s.text for s in segs if s.kind is SegmentKind.PROSE)
        assert "before" in prose_text
        assert "after" in prose_text

    @pytest.mark.unit
    def test_inline_code_is_code(self) -> None:
        text = "use the `parser` here"
        segs = segment(text, OutputChannel.PR_BODY)
        code = [s for s in segs if s.kind is SegmentKind.CODE]
        assert any("parser" in s.text for s in code)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "text",
        [
            "plain",
            "a `b` c",
            "before\n```\ncode\n```\nafter\n",
            "line with ` unbalanced backtick",
            "multi\nline\nprose\n",
        ],
    )
    def test_segments_cover_input(self, text: str) -> None:
        assert _covers(text, OutputChannel.DELIVERABLE)

    @pytest.mark.unit
    def test_offsets_map_back_to_text(self) -> None:
        text = "alpha `beta` gamma"
        for seg in segment(text, OutputChannel.MESSAGE):
            assert text[seg.start : seg.end] == seg.text
