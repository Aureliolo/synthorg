"""Repository protocol for custom signal rule persistence."""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.meta.rules.custom import CustomRuleDefinition  # noqa: TC001


@runtime_checkable
class CustomRuleRepository(Protocol):
    """Persistence interface for user-defined declarative rules.

    Implementations provide CRUD operations for
    ``CustomRuleDefinition`` objects.
    """

    async def save(self, rule: CustomRuleDefinition) -> None:
        """Persist a custom rule (insert or update by id).

        Args:
            rule: The rule definition to persist.

        Raises:
            ConstraintViolationError: If the rule name conflicts
                with an existing rule.
            QueryError: If the operation fails.
        """
        ...

    async def get(
        self,
        rule_id: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        """Retrieve a custom rule by id.

        Args:
            rule_id: UUID string of the rule.

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

    async def list_rules(
        self,
        *,
        enabled_only: bool = False,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules ordered by name.

        Args:
            enabled_only: If ``True``, return only enabled rules.

        Returns:
            Tuple of rule definitions.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def delete(self, rule_id: NotBlankStr) -> bool:
        """Delete a custom rule by id.

        Args:
            rule_id: UUID string of the rule.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...
