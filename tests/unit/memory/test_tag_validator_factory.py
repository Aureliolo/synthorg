"""Tests for ``deduplicate_tags`` and the field-validator pattern that wraps it."""

import pytest
from pydantic import BaseModel, ConfigDict, field_validator

from synthorg.memory.utils import deduplicate_tags


class _DedupOnly(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    @field_validator("tags", mode="after")
    @classmethod
    def _dedup(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return deduplicate_tags(value)


class _DedupAndTruncate(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    @field_validator("tags", mode="after")
    @classmethod
    def _dedup(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return deduplicate_tags(value)[:3]


@pytest.mark.unit
class TestDeduplicateTagsFieldValidator:
    def test_dedupes_input(self) -> None:
        m = _DedupOnly(tags=("a", "b", "a", "c", "b"))
        assert m.tags == ("a", "b", "c")

    def test_passthrough_when_already_unique(self) -> None:
        m = _DedupOnly(tags=("a", "b", "c"))
        assert m.tags == ("a", "b", "c")

    def test_empty_passes(self) -> None:
        m = _DedupOnly()
        assert m.tags == ()

    def test_truncates_after_dedupe(self) -> None:
        m = _DedupAndTruncate(tags=("a", "a", "b", "c", "d", "e"))
        assert m.tags == ("a", "b", "c")

    def test_no_truncate_when_under_limit(self) -> None:
        m = _DedupAndTruncate(tags=("a", "a", "b"))
        assert m.tags == ("a", "b")

    def test_helper_returns_tuple(self) -> None:
        """Helper preserves order and yields a tuple of unique entries."""
        assert deduplicate_tags(("a", "b", "a", "c")) == ("a", "b", "c")
        assert deduplicate_tags(()) == ()
