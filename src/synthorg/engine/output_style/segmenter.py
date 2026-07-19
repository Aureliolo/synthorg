# module-kind: code
"""Deterministic segmentation of agent output into prose vs code spans.

Segmentation never exempts a violation; it only lets the evaluator pick a safe
enforcement action. An ``AUTO_REWRITE`` rule may deterministically fix a match
in a PROSE span, but a match in a CODE span is rejected rather than rewritten
because a punctuation swap could corrupt code or string data.

Code channels (commit messages, code files) are treated as a single code span.
Prose channels (deliverables, messages, PR/issue bodies) are Markdown: fenced
blocks and inline-code spans are code; everything else is prose. Segments cover
the input contiguously so the rewriter can reconstruct the text by concatenation.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.engine.output_style.models import (
    CODE_CHANNELS,
    OutputChannel,
    SegmentKind,
)

_FENCE_MARKERS: tuple[str, ...] = ("```", "~~~")
_BACKTICK: str = "`"


class Segment(BaseModel):
    """A contiguous span of output classified as prose or code.

    Attributes:
        text: The span's exact substring of the original output.
        kind: Whether the span is prose or code.
        start: Inclusive start offset into the original output.
        end: Exclusive end offset into the original output.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: str
    kind: SegmentKind
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_span(self) -> Self:
        """Reject a span whose bounds or length are inconsistent.

        Returns:
            The validated instance.

        Raises:
            ValueError: If ``end`` precedes ``start`` or ``text`` length does
                not equal ``end - start``.
        """
        if self.end < self.start:
            msg = f"Segment end ({self.end}) precedes start ({self.start})"
            raise ValueError(msg)
        if len(self.text) != self.end - self.start:
            msg = (
                f"Segment text length ({len(self.text)}) does not match span "
                f"{self.end - self.start} ([{self.start}, {self.end}))"
            )
            raise ValueError(msg)
        return self


def _scan_inline_code(line: str, base: int) -> list[Segment]:
    """Split a single prose line into prose / inline-code segments.

    Args:
        line: The line text (may include its trailing newline).
        base: Absolute offset of the line's first character.

    Returns:
        Contiguous segments covering the line.
    """
    segments: list[Segment] = []
    run_start = 0
    in_code = False
    i = 0
    while i < len(line):
        if line[i] == _BACKTICK:
            if i > run_start:
                segments.append(
                    Segment(
                        text=line[run_start:i],
                        kind=SegmentKind.CODE if in_code else SegmentKind.PROSE,
                        start=base + run_start,
                        end=base + i,
                    )
                )
            # The backtick itself is inert punctuation; attach it to a code span
            # so it is never treated as prose to rewrite.
            segments.append(
                Segment(
                    text=_BACKTICK,
                    kind=SegmentKind.CODE,
                    start=base + i,
                    end=base + i + 1,
                )
            )
            run_start = i + 1
            in_code = not in_code
        i += 1
    if run_start < len(line):
        segments.append(
            Segment(
                text=line[run_start:],
                kind=SegmentKind.CODE if in_code else SegmentKind.PROSE,
                start=base + run_start,
                end=base + len(line),
            )
        )
    return segments


def segment(text: str, channel: OutputChannel) -> tuple[Segment, ...]:
    """Segment agent output into contiguous prose / code spans.

    Args:
        text: The agent-produced output.
        channel: The output boundary, which decides code-channel handling.

    Returns:
        Contiguous segments covering ``text``; empty when ``text`` is empty.
    """
    if not text:
        return ()
    if channel in CODE_CHANNELS:
        return (Segment(text=text, kind=SegmentKind.CODE, start=0, end=len(text)),)

    segments: list[Segment] = []
    pos = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        is_fence = any(stripped.startswith(m) for m in _FENCE_MARKERS)
        if is_fence:
            segments.append(
                Segment(
                    text=line,
                    kind=SegmentKind.CODE,
                    start=pos,
                    end=pos + len(line),
                )
            )
            in_fence = not in_fence
        elif in_fence:
            segments.append(
                Segment(
                    text=line,
                    kind=SegmentKind.CODE,
                    start=pos,
                    end=pos + len(line),
                )
            )
        else:
            segments.extend(_scan_inline_code(line, pos))
        pos += len(line)
    return tuple(segments)


__all__ = ["Segment", "segment"]
