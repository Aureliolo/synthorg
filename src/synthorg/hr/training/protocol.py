"""Training mode protocol interfaces.

Defines the four pluggable extension points for the training pipeline:
content extraction, source selection, curation, and guards.
All protocols are ``@runtime_checkable`` for duck-typing support.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
)


@runtime_checkable
class ContentExtractor(Protocol):
    """Extracts candidate training items from a senior agent's knowledge.

    Each extractor is keyed by ``content_type`` and returns unranked
    candidate items that flow to the curation stage.
    """

    @property
    def content_type(self) -> ContentType:
        """The content type this extractor produces."""
        ...

    async def extract(
        self,
        *,
        source_agent_ids: tuple[NotBlankStr, ...],
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
    ) -> tuple[TrainingItem, ...]:
        """Extract candidate items from source agents.

        Args:
            source_agent_ids: Senior agents to extract from.
            new_agent_role: Role of the new hire (for relevance).
            new_agent_level: Seniority level of the new hire.

        Returns:
            Unranked candidate training items.
        """
        ...


@runtime_checkable
class SourceSelector(Protocol):
    """Selects senior agents as knowledge sources for training."""

    @property
    def name(self) -> str:
        """Selector strategy name."""
        ...

    async def select(
        self,
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
        new_agent_department: NotBlankStr | None = None,
    ) -> tuple[NotBlankStr, ...]:
        """Select source agent IDs for training.

        Args:
            new_agent_role: Role of the new hire.
            new_agent_level: Seniority level of the new hire.
            new_agent_department: Department of the new hire. Required
                for department-scoped selectors; optional otherwise.

        Returns:
            Ordered tuple of selected agent IDs.
        """
        ...


@runtime_checkable
class CurationStrategy(Protocol):
    """Reduces candidate items to a curated set for seeding.

    Returned items must have updated ``relevance_score`` and
    be ordered descending by score.
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        ...

    async def curate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        new_agent_role: NotBlankStr,
        new_agent_level: SeniorityLevel,
        content_type: ContentType,
    ) -> tuple[TrainingItem, ...]:
        """Curate candidate items for a content type.

        Args:
            items: Unranked candidate items.
            new_agent_role: Role of the new hire.
            new_agent_level: Seniority level of the new hire.
            content_type: The content type being curated.

        Returns:
            Ranked items with updated relevance_score, descending.
        """
        ...


@runtime_checkable
class TrainingGuard(Protocol):
    """Evaluates training items against safety and approval policies.

    Guards apply in sequence: each guard receives the output of
    the previous guard's ``approved_items``.
    """

    @property
    def name(self) -> str:
        """Guard name."""
        ...

    async def evaluate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        content_type: ContentType,
        plan: TrainingPlan,
    ) -> TrainingGuardDecision:
        """Evaluate items against this guard's policy.

        Args:
            items: Items to evaluate.
            content_type: The content type being evaluated.
            plan: The training plan (for config access).

        Returns:
            Decision with approved items and rejection details.
        """
        ...
