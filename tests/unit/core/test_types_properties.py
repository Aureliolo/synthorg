"""Property-based tests for custom type validators (NotBlankStr)."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class _NotBlankModel(BaseModel):
    value: NotBlankStr


class TestNotBlankStrProperties:
    @given(
        text=st.text(min_size=1).filter(lambda s: s.strip()),
    )
    @settings(max_examples=200)
    def test_valid_strings_accepted(self, text: str) -> None:
        model = _NotBlankModel(value=text)
        assert model.value == text

    @given(
        text=st.text(
            alphabet=st.sampled_from([" ", "\t", "\n", "\r", "\x0b", "\x0c"]),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_whitespace_only_rejected(self, text: str) -> None:
        with pytest.raises(ValidationError):
            _NotBlankModel(value=text)

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _NotBlankModel(value="")

    @given(
        prefix=st.text(
            alphabet=st.sampled_from([" ", "\t", "\n"]),
            max_size=5,
        ),
        core=st.text(min_size=1, max_size=20).filter(lambda s: s.strip()),
        suffix=st.text(
            alphabet=st.sampled_from([" ", "\t", "\n"]),
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_strings_with_non_whitespace_core_accepted(
        self,
        prefix: str,
        core: str,
        suffix: str,
    ) -> None:
        text = prefix + core + suffix
        model = _NotBlankModel(value=text)
        assert model.value == text
