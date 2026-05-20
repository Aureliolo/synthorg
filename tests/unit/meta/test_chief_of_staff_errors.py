"""Unit tests for the conversational Chief of Staff domain errors."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.meta.errors import (
    ChiefOfStaffError,
    ConversationalProposeResponseInvalidError,
    ConversationalProposeUnavailableError,
    ConversationClosedError,
    ConversationNotFoundError,
)

pytestmark = pytest.mark.unit


class TestChiefOfStaffErrors:
    """Status / category / code wiring for the propose-path errors.

    Importing the module already proves ``DomainError.__init_subclass__``
    accepted every error_code/category pairing (a mismatch raises
    ``TypeError`` at class-definition time).
    """

    def test_base_is_domain_error(self) -> None:
        assert issubclass(ChiefOfStaffError, DomainError)

    def test_conversation_not_found(self) -> None:
        exc = ConversationNotFoundError(conversation_id="conv-1")
        assert isinstance(exc, ChiefOfStaffError)
        assert exc.status_code == 404
        assert exc.error_category is ErrorCategory.NOT_FOUND
        assert exc.error_code is ErrorCode.CONVERSATION_NOT_FOUND
        assert exc.conversation_id == "conv-1"
        # Generic user-facing message; no id leakage.
        assert "conv-1" not in str(exc)

    def test_conversation_closed(self) -> None:
        exc = ConversationClosedError(conversation_id="conv-2")
        assert exc.status_code == 409
        assert exc.error_category is ErrorCategory.CONFLICT
        assert exc.error_code is ErrorCode.CONVERSATION_CLOSED
        assert exc.conversation_id == "conv-2"
        assert "conv-2" not in str(exc)

    def test_propose_unavailable(self) -> None:
        exc = ConversationalProposeUnavailableError()
        assert exc.status_code == 503
        assert exc.error_category is ErrorCategory.INTERNAL
        assert exc.error_code is ErrorCode.SERVICE_UNAVAILABLE

    def test_propose_response_invalid(self) -> None:
        exc = ConversationalProposeResponseInvalidError()
        assert exc.status_code == 502
        assert exc.error_category is ErrorCategory.PROVIDER_ERROR
        assert exc.error_code is ErrorCode.CONVERSATIONAL_PROPOSE_RESPONSE_INVALID

    def test_raisable_and_catchable_as_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise ConversationNotFoundError(conversation_id="x")
