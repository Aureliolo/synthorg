"""Tests for ``dedupe_tags_in_place``."""

from typing import Self

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from synthorg.memory.utils import dedupe_tags_in_place


class _DedupOnly(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _dedup(self) -> Self:
        dedupe_tags_in_place(self)
        return self


class _DedupAndTruncate(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _dedup(self) -> Self:
        dedupe_tags_in_place(self, max_items=3)
        return self


@pytest.mark.unit
class TestDedupeTagsInPlace:
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
