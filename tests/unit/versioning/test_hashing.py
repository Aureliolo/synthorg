"""Tests for compute_content_hash."""

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.versioning.hashing import compute_content_hash


class _Simple(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    name: str
    value: int


class _WithEnum(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    name: str
    tags: tuple[str, ...]


class TestComputeContentHash:
    """Hash properties: determinism, sensitivity, and format."""

    @pytest.mark.unit
    def test_returns_64_char_hex(self) -> None:
        h = compute_content_hash(_Simple(name="a", value=1))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @pytest.mark.unit
    def test_same_content_same_hash(self) -> None:
        a = _Simple(name="test", value=42)
        b = _Simple(name="test", value=42)
        assert compute_content_hash(a) == compute_content_hash(b)

    @pytest.mark.unit
    def test_different_content_different_hash(self) -> None:
        a = _Simple(name="test", value=42)
        b = _Simple(name="test", value=43)
        assert compute_content_hash(a) != compute_content_hash(b)

    @pytest.mark.unit
    def test_field_name_change_changes_hash(self) -> None:
        a = _Simple(name="alice", value=1)
        b = _Simple(name="bob", value=1)
        assert compute_content_hash(a) != compute_content_hash(b)

    @pytest.mark.unit
    def test_deterministic_across_calls(self) -> None:
        model = _Simple(name="stable", value=99)
        hashes = [compute_content_hash(model) for _ in range(10)]
        assert len(set(hashes)) == 1

    @pytest.mark.unit
    def test_tuple_ordering_matters(self) -> None:
        a = _WithEnum(name="x", tags=("a", "b"))
        b = _WithEnum(name="x", tags=("b", "a"))
        assert compute_content_hash(a) != compute_content_hash(b)

    @pytest.mark.unit
    def test_empty_tuple_vs_non_empty(self) -> None:
        a = _WithEnum(name="x", tags=())
        b = _WithEnum(name="x", tags=("a",))
        assert compute_content_hash(a) != compute_content_hash(b)
