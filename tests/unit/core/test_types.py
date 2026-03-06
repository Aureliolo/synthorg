"""Tests for core type annotations and validation helpers."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from ai_company.core.types import NotBlankStr, validate_unique_strings

pytestmark = pytest.mark.timeout(30)


# ── Test models ─────────────────────────────────────────────────


class _ScalarModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: NotBlankStr


class _OptionalModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: NotBlankStr | None = None


class _TupleModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    values: tuple[NotBlankStr, ...]


# ── NotBlankStr (scalar) ────────────────────────────────────────


@pytest.mark.unit
class TestNotBlankStr:
    def test_accepts_valid_string(self) -> None:
        m = _ScalarModel(value="hello")
        assert m.value == "hello"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _ScalarModel(value="")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _ScalarModel(value="   ")

    def test_rejects_tabs_only(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _ScalarModel(value="\t\t")

    def test_rejects_newlines_only(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _ScalarModel(value="\n\n")

    def test_accepts_string_with_spaces(self) -> None:
        m = _ScalarModel(value="  hello  ")
        assert m.value == "  hello  "


# ── NotBlankStr | None ──────────────────────────────────────────


@pytest.mark.unit
class TestNotBlankStrOptional:
    def test_accepts_none(self) -> None:
        m = _OptionalModel(value=None)
        assert m.value is None

    def test_accepts_valid_string(self) -> None:
        m = _OptionalModel(value="hello")
        assert m.value == "hello"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _OptionalModel(value="")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _OptionalModel(value="   ")


# ── tuple[NotBlankStr, ...] ─────────────────────────────────────


@pytest.mark.unit
class TestNotBlankStrTuple:
    def test_accepts_valid_tuple(self) -> None:
        m = _TupleModel(values=("a", "b", "c"))
        assert m.values == ("a", "b", "c")

    def test_accepts_empty_tuple(self) -> None:
        m = _TupleModel(values=())
        assert m.values == ()

    def test_rejects_empty_element(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _TupleModel(values=("valid", ""))

    def test_rejects_whitespace_element(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _TupleModel(values=("valid", "  "))

    def test_per_element_validation(self) -> None:
        """Each element is validated independently."""
        with pytest.raises(ValidationError):
            _TupleModel(values=("", "  ", "valid"))


# ── validate_unique_strings ─────────────────────────────────────


@pytest.mark.unit
class TestValidateUniqueStrings:
    def test_accepts_unique(self) -> None:
        validate_unique_strings(("a", "b", "c"), "test_field")

    def test_accepts_empty(self) -> None:
        validate_unique_strings((), "test_field")

    def test_accepts_single(self) -> None:
        validate_unique_strings(("a",), "test_field")

    def test_rejects_duplicates(self) -> None:
        with pytest.raises(ValueError, match="Duplicate entries in test_field"):
            validate_unique_strings(("a", "b", "a"), "test_field")

    def test_reports_all_duplicates(self) -> None:
        with pytest.raises(ValueError, match=r"'a'.*'b'|'b'.*'a'"):
            validate_unique_strings(("a", "b", "a", "b"), "test_field")
