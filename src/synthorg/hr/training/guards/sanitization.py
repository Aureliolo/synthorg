"""Sanitization guard for training mode.

Mandatory, non-bypassable guard that redacts sensitive content
from training items using the shared sanitize_message utility.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.sanitization import sanitize_message
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
)
from synthorg.observability import get_logger
from synthorg.observability.events.training import (
    HR_TRAINING_SANITIZATION_APPLIED,
)

if TYPE_CHECKING:
    from synthorg.hr.training.models import TrainingPlan

logger = get_logger(__name__)

_DEFAULT_MAX_LENGTH: Final[int] = 2000


class SanitizationGuard:
    """Mandatory sanitization guard.

    Runs ``sanitize_message()`` on each item's content. Items whose
    content is fully redacted (becomes ``"details redacted"`` or has
    no alphanumeric characters) are rejected.

    Args:
        max_length: Maximum content length after sanitization.
    """

    def __init__(
        self,
        *,
        max_length: int = _DEFAULT_MAX_LENGTH,
    ) -> None:
        if max_length < 0:
            msg = f"max_length must be a non-negative integer, got {max_length}"
            raise ValueError(msg)
        self._max_length = max_length

    @property
    def name(self) -> str:
        """Guard name."""
        return "sanitization"

    async def evaluate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        content_type: ContentType,
        plan: TrainingPlan,  # noqa: ARG002
    ) -> TrainingGuardDecision:
        """Sanitize all items, rejecting fully redacted ones.

        Args:
            items: Items to sanitize.
            content_type: Content type being evaluated.
            plan: Training plan (unused).

        Returns:
            Decision with sanitized approved items.
        """
        if not items:
            return TrainingGuardDecision(
                approved_items=(),
                rejected_count=0,
                guard_name="sanitization",
            )

        approved: list[TrainingItem] = []
        rejected_reasons: list[str] = []

        for item in items:
            sanitized = sanitize_message(
                item.content,
                max_length=self._max_length,
            )
            if sanitized == "details redacted":
                rejected_reasons.append(
                    f"Content fully redacted by sanitizer for item {item.id}",
                )
            elif not any(c.isalnum() for c in sanitized):
                rejected_reasons.append(
                    f"Content lacks alphanumeric content for item {item.id}",
                )
            else:
                approved.append(
                    item.model_copy(update={"content": sanitized}),
                )

        rejected_count = len(items) - len(approved)

        logger.debug(
            HR_TRAINING_SANITIZATION_APPLIED,
            content_type=content_type.value,
            input_count=len(items),
            approved_count=len(approved),
            rejected_count=rejected_count,
        )

        return TrainingGuardDecision(
            approved_items=tuple(approved),
            rejected_count=rejected_count,
            guard_name="sanitization",
            rejection_reasons=tuple(rejected_reasons),
        )
