"""WebhookActivityService: read-only facade over the webhook receipt log.

The webhooks API controller previously reached into
``state["app_state"].persistence.webhook_receipts`` to list activity.
That bypassed the integrations service layer and silently expanded the
controller-to-repository contact surface.

This service owns the persistence access for the read-only activity
endpoint, validates ``limit`` at the boundary, and emits a structured
audit event on every call so the audit chain captures every read of
the durable receipt log.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.domain_errors import ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import WEBHOOK_ACTIVITY_LISTED

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.connections.models import WebhookReceipt
    from synthorg.persistence.connection_protocol import WebhookReceiptRepository

logger = get_logger(__name__)

_MIN_LIMIT: Final[int] = 1
_MAX_LIMIT: Final[int] = 500


class WebhookActivityService:
    """Service facade for listing webhook receipt activity.

    Args:
        receipts_repo: Backing :class:`WebhookReceiptRepository`. The
            service does not own the repo's lifecycle; the application
            wiring supplies the repository instance from the connected
            persistence backend at startup.
    """

    def __init__(self, *, receipts_repo: WebhookReceiptRepository) -> None:
        self._receipts_repo = receipts_repo

    async def list_activity(
        self,
        *,
        connection_name: NotBlankStr,
        limit: int,
    ) -> tuple[WebhookReceipt, ...]:
        """Return the most recent receipts for a connection, newest-first.

        Args:
            connection_name: Connection to filter on. The receipt repo
                requires a non-blank string; passing a blank value here
                raises a :class:`ValidationError`.
            limit: Maximum number of receipts to return. Must be in
                ``[1, 500]``.

        Returns:
            Tuple of :class:`WebhookReceipt` rows ordered newest-first.

        Raises:
            ValidationError: If ``limit`` is outside ``[1, 500]``.
        """
        if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
            msg = f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}; got {limit}"
            raise ValidationError(msg)
        receipts = await self._receipts_repo.get_by_connection(
            connection_name,
            limit=limit,
        )
        logger.info(
            WEBHOOK_ACTIVITY_LISTED,
            connection_name=str(connection_name),
            limit=limit,
            count=len(receipts),
        )
        return receipts


__all__ = ["WebhookActivityService"]
