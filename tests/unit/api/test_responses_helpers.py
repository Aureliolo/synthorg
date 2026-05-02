"""Unit tests for :func:`synthorg.api.responses.require_resource_or_404`.

The helper centralises the ``if resource is None: log + raise``
pattern that recurred across many controllers.  These tests pin:

- Happy path: returns the value unchanged when not ``None``.
- Sad path: raises :class:`NotFoundError` with the
  ``resource_type {identifier!r} not found`` message.
- The raised error's instance ``error_code`` reflects the supplied
  ``code`` (so domain-specific 404 codes survive the centralisation).
- The default ``code`` is the generic ``RESOURCE_NOT_FOUND`` so
  callers without a dedicated code keep working.
- The factory call rejects non-NOT_FOUND-band codes (defence in
  depth: ``resource_not_found`` itself enforces the band; the
  helper just forwards).
"""

import pytest

from synthorg.api.responses import require_resource_or_404
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.error_taxonomy import ErrorCode

pytestmark = pytest.mark.unit


class TestHappyPath:
    def test_returns_resource_unchanged_when_not_none(self) -> None:
        result = require_resource_or_404(
            "found-value",
            resource_type="Artifact",
            identifier="abc-123",
            log_event="api.resource.not_found",
        )
        assert result == "found-value"

    def test_returns_falsy_value_unchanged(self) -> None:
        """``None`` is the only sentinel; falsy values pass through."""
        assert (
            require_resource_or_404(
                0,
                resource_type="counter",
                identifier="x",
                log_event="api.resource.not_found",
            )
            == 0
        )
        assert (
            require_resource_or_404(
                "",
                resource_type="counter",
                identifier="x",
                log_event="api.resource.not_found",
            )
            == ""
        )
        assert (
            require_resource_or_404(
                False,
                resource_type="counter",
                identifier="x",
                log_event="api.resource.not_found",
            )
            is False
        )


class TestSadPath:
    def test_raises_not_found_with_generic_message(self) -> None:
        with pytest.raises(NotFoundError) as exc_info:
            require_resource_or_404(
                None,
                resource_type="Artifact",
                identifier="abc-123",
                log_event="api.resource.not_found",
            )
        assert str(exc_info.value) == "Artifact 'abc-123' not found"

    def test_default_code_is_generic_resource_not_found(self) -> None:
        with pytest.raises(NotFoundError) as exc_info:
            require_resource_or_404(
                None,
                resource_type="Artifact",
                identifier="abc-123",
                log_event="api.resource.not_found",
            )
        # ``resource_not_found`` mutates the *instance* attribute; the
        # ClassVar default stays RESOURCE_NOT_FOUND so the assertion
        # uses the instance attribute.
        assert exc_info.value.error_code == ErrorCode.RESOURCE_NOT_FOUND

    def test_custom_code_overrides_default(self) -> None:
        with pytest.raises(NotFoundError) as exc_info:
            require_resource_or_404(
                None,
                resource_type="task",
                identifier="task-1",
                log_event="api.resource.not_found",
                code=ErrorCode.TASK_NOT_FOUND,
            )
        assert exc_info.value.error_code == ErrorCode.TASK_NOT_FOUND

    def test_non_not_found_band_code_is_rejected(self) -> None:
        """``resource_not_found`` enforces the 3xxx band; helper just forwards."""
        with pytest.raises(ValueError, match="NOT_FOUND"):
            require_resource_or_404(
                None,
                resource_type="task",
                identifier="task-1",
                log_event="api.resource.not_found",
                # CONFLICT-band code (4xxx) -- helper forwards to the
                # factory which rejects it.
                code=ErrorCode.RESOURCE_CONFLICT,
            )


class TestExtraLogKwargs:
    def test_extra_kwargs_pass_through(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"), pytest.raises(NotFoundError):
            require_resource_or_404(
                None,
                resource_type="task",
                identifier="task-1",
                log_event="api.resource.not_found",
                extra_log_kwargs={"reason": "wrong_owner"},
            )
        # Stable keys must not be clobberable from extra_log_kwargs.
        with caplog.at_level("WARNING"), pytest.raises(NotFoundError):
            require_resource_or_404(
                None,
                resource_type="task",
                identifier="task-1",
                log_event="api.resource.not_found",
                extra_log_kwargs={"id": "WRONG", "resource": "WRONG"},
            )
        # No assertion on caplog records: structlog routing is exercised
        # end-to-end by integration tests; we just verify the helper
        # accepts the kwargs without error and the not-found still fires.
