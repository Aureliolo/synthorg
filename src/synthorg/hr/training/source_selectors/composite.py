"""Composite source selector.

Combines multiple selectors, merges their results, and
deduplicates agent IDs while preserving order.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.training import (
    HR_TRAINING_SELECTION_COMPLETE,
    HR_TRAINING_SELECTOR_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from synthorg.hr.seniority import SeniorityLevel
    from synthorg.hr.training.protocol import SourceSelector

logger = get_logger(__name__)


class CompositeSelector:
    """Combine multiple selectors with weighted voting.

    Runs all child selectors, merges results, and deduplicates.
    Weights are stored for future scoring but currently all
    selector outputs are merged equally.

    Args:
        selectors: Child selectors.
        weights: Per-selector weights (must match selectors length).
    """

    def __init__(
        self,
        *,
        selectors: tuple[SourceSelector, ...],
        weights: tuple[float, ...],
    ) -> None:
        if len(selectors) != len(weights):
            msg = (
                f"CompositeSelector requires matching selectors/weights "
                f"lengths, got selectors={len(selectors)}, "
                f"weights={len(weights)}"
            )
            logger.warning(
                HR_TRAINING_SELECTOR_CONFIG_INVALID,
                selector_type="composite",
                reason=msg,
                selectors_len=len(selectors),
                weights_len=len(weights),
            )
            raise ValueError(msg)
        self._selectors = selectors
        self._weights = weights

    @property
    def name(self) -> str:
        """Selector strategy name."""
        return "composite"

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
        new_agent_department: NotBlankStr | None = None,
    ) -> tuple[NotBlankStr, ...]:
        """Run all child selectors and merge results.

        Args:
            new_agent_role: Role of the new hire.
            new_agent_level: Seniority level.
            new_agent_department: Department of the new hire.

        Returns:
            Deduplicated merged agent IDs.
        """
        if not self._selectors:
            return ()

        seen: set[str] = set()
        result: list[NotBlankStr] = []

        for selector in self._selectors:
            ids = await selector.select(
                new_agent_role=new_agent_role,
                new_agent_level=new_agent_level,
                new_agent_department=new_agent_department,
            )
            for agent_id in ids:
                str_id = str(agent_id)
                if str_id not in seen:
                    seen.add(str_id)
                    result.append(agent_id)

        logger.info(
            HR_TRAINING_SELECTION_COMPLETE,
            selector="composite",
            child_count=len(self._selectors),
            total_selected=len(result),
        )
        return tuple(result)
