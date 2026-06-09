"""Repository protocol for custom signal rule persistence."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.rules.custom import CustomRuleDefinition
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class CustomRuleFilterSpec(BaseModel):
    """Filter spec for ``CustomRuleRepository.query`` (ADR-0001).

    ``enabled_only`` restricts results to rules with ``enabled=True``;
    defaults to ``False`` so an empty spec matches every rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    enabled_only: bool = Field(default=False)


@runtime_checkable
class CustomRuleRepository(
    IdKeyedRepository[CustomRuleDefinition, NotBlankStr],
    FilteredQueryRepository[CustomRuleDefinition, CustomRuleFilterSpec],
    Protocol,
):
    """Persistence interface for user-defined declarative rules.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Bespoke per D7: :meth:`get_by_name` is an alternate-key
    lookup on the ``name`` UNIQUE column that callers use to validate
    name conflicts before save; routing through ``query`` for a
    single-row lookup is wasteful.
    """

    @override
    async def save(self, entity: CustomRuleDefinition, /) -> None:
        """Persist a custom rule (insert or update by id).

        Args:
            entity: The rule definition to persist.

        Raises:
            ConstraintViolationError: If the rule name conflicts
                with an existing rule.
            QueryError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> CustomRuleDefinition | None:
        """Retrieve a custom rule by id.

        Args:
            entity_id: UUID string of the rule.

        Returns:
            The rule definition, or ``None`` if not found.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def get_by_name(
        self,
        name: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        """Retrieve a custom rule by name.

        Args:
            name: Unique rule name.

        Returns:
            The rule definition, or ``None`` if not found.

        Raises:
            QueryError: If the query fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules in ``name`` order.

        Args:
            limit: Maximum rules to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Rules ordered by ``name`` ascending.

        Raises:
            QueryError: If the query fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: CustomRuleFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules matching ``filter_spec`` ordered by name.

        Args:
            filter_spec: Carries optional ``enabled_only`` filter.
            limit: Maximum rules to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Rules ordered by ``name`` ascending.

        Raises:
            QueryError: If the query fails.
        """
        ...

    @override
    async def count(self, filter_spec: CustomRuleFilterSpec) -> int:
        """Count custom rules matching the filter spec."""
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a custom rule by id.

        Args:
            entity_id: UUID string of the rule.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...
