"""Tests for ``make_dedupe_tags_model_validator``."""

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from synthorg.memory.utils import make_dedupe_tags_model_validator


class _DedupOnly(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    _dedup = model_validator(mode="after")(make_dedupe_tags_model_validator())


class _DedupAndTruncate(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tags: tuple[str, ...] = ()

    _dedup = model_validator(mode="after")(
        make_dedupe_tags_model_validator(max_items=3)
    )


@pytest.mark.unit
class TestDedupeTagsValidator:
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
