"""Unit tests for run-narrative domain errors."""

import pytest

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.meta.chief_of_staff.narrative.errors import (
    NarrativeGenerationError,
    NarrativeSourceUnavailableError,
)

pytestmark = pytest.mark.unit


class TestNarrativeErrors:
    def test_source_unavailable_is_internal(self) -> None:
        err = NarrativeSourceUnavailableError()
        assert err.error_category is ErrorCategory.INTERNAL
        assert err.error_code is ErrorCode.NARRATIVE_SOURCE_UNAVAILABLE
        assert err.retryable is False

    def test_generation_error_is_retryable_internal(self) -> None:
        err = NarrativeGenerationError()
        assert err.error_category is ErrorCategory.INTERNAL
        assert err.error_code is ErrorCode.NARRATIVE_GENERATION_ERROR
        assert err.retryable is True

    def test_codes_are_in_internal_band(self) -> None:
        assert str(ErrorCode.NARRATIVE_SOURCE_UNAVAILABLE.value).startswith("8")
        assert str(ErrorCode.NARRATIVE_GENERATION_ERROR.value).startswith("8")
