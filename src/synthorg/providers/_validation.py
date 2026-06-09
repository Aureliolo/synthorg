# module-kind: code
"""Input validation for completion-provider calls.

Stateless guards extracted from ``BaseCompletionProvider`` and shared
with driver subclasses (e.g. the cassette wrapper) that validate before
delegating to a hook.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_CALL_ERROR

from .errors import InvalidRequestError
from .models import ChatMessage

logger = get_logger(__name__)


def validate_messages(messages: list[ChatMessage]) -> None:
    """Reject empty message lists.

    Args:
        messages: Conversation messages.

    Raises:
        InvalidRequestError: If no messages are provided.
    """
    if not messages:
        msg = "messages must not be empty"
        logger.error(PROVIDER_CALL_ERROR, error="messages must not be empty")
        raise InvalidRequestError(msg, context={"field": "messages"})


def validate_model(model: str) -> None:
    """Reject blank, empty, or non-string model identifiers.

    Args:
        model: Model identifier string.

    Raises:
        InvalidRequestError: If model is not a string, empty,
            or whitespace-only.
    """
    if not isinstance(model, str) or not model.strip():
        msg = "model must be a non-blank string"
        logger.error(
            PROVIDER_CALL_ERROR,
            error="model must be a non-blank string",
            received_type=type(model).__name__,
        )
        raise InvalidRequestError(
            msg,
            context={
                "field": "model",
                "received_type": type(model).__name__,
            },
        )
