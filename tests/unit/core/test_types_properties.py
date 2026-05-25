"""Property-based tests for custom type validators (NotBlankStr, CurrencyCode)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.budget.currency import CURRENCY_SYMBOLS, MINOR_UNITS, CurrencyCode
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_KNOWN_CODES = sorted(frozenset(CURRENCY_SYMBOLS) | frozenset(MINOR_UNITS))


class _NotBlankModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: NotBlankStr


class _CurrencyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    currency: CurrencyCode


class TestNotBlankStrProperties:
    @given(
        text=st.text(min_size=1).filter(lambda s: s.strip()),
    )
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
    def test_strings_with_non_whitespace_core_accepted(
        self,
        prefix: str,
        core: str,
        suffix: str,
    ) -> None:
        text = prefix + core + suffix
        model = _NotBlankModel(value=text)
        assert model.value == text


class TestCurrencyCodeProperties:
    """Property-based coverage for the ``CurrencyCode`` validator."""

    @given(code=st.sampled_from(_KNOWN_CODES))
    def test_every_allowlisted_code_round_trips(self, code: str) -> None:
        """Any code in the allowlist parses, keeps its value, and stays a str."""
        model = _CurrencyModel(currency=code)
        assert model.currency == code
        assert isinstance(model.currency, str)

    @given(
        text=st.text(
            alphabet=st.characters(
                min_codepoint=0x41,
                max_codepoint=0x5A,
            ),
            min_size=3,
            max_size=3,
        ),
    )
    def test_unknown_three_letter_codes_rejected(self, text: str) -> None:
        """Arbitrary 3-uppercase-letter strings are rejected unless allowlisted."""
        if text in _KNOWN_CODES:
            return
        with pytest.raises(ValidationError):
            _CurrencyModel(currency=text)

    @given(
        size=st.integers(min_value=0, max_value=10).filter(lambda n: n != 3),
    )
    def test_wrong_length_rejected(self, size: int) -> None:
        """Strings whose length != 3 are rejected regardless of content."""
        text = "A" * size
        with pytest.raises(ValidationError):
            _CurrencyModel(currency=text)

    @given(code=st.sampled_from(_KNOWN_CODES))
    def test_lowercase_variant_rejected(self, code: str) -> None:
        """Allowlisted codes in lowercase are rejected by the pattern."""
        lower = code.lower()
        if lower == code:
            # Skip codes that are unchanged by ``str.lower()`` (already
            # lowercase, no letter characters, or composed of characters
            # whose case folding is a no-op).  ISO 4217 codes are
            # alphabetic and uppercase, so this branch is defensive
            # against future additions to ``_KNOWN_CODES`` -- the test
            # only asserts rejection for codes that actually change
            # under lowering.
            return
        with pytest.raises(ValidationError):
            _CurrencyModel(currency=lower)
