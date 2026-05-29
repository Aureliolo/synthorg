"""Principle-override repository protocol and domain model.

The rollback executor's ``PromptMutator`` persists restored principle
text through this repository so the YAML-pack-loaded principles can be
overlaid at runtime. The store is keyed by ``scope`` (the principle
identifier from the YAML packs) and carries a ``restored_from`` field
recording the rollback operation that produced it for forensic audit.
"""

from typing import Protocol, override, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


class PrincipleOverride(BaseModel):
    """A single restored principle that overlays the YAML packs."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    scope: NotBlankStr = Field(description="Principle scope identifier")
    text: NotBlankStr = Field(description="Override principle text")
    restored_from: NotBlankStr = Field(
        description=("Provenance of the override (e.g. rollback operation id)."),
    )
    created_at: AwareDatetime = Field(
        description="When the override was first written",
    )
    updated_at: AwareDatetime = Field(
        description="When the override was last refreshed",
    )


@runtime_checkable
class PrincipleOverrideRepository(
    IdKeyedRepository[PrincipleOverride, NotBlankStr],
    Protocol,
):
    """CRUD interface for principle overrides.

    Composes :class:`IdKeyedRepository` (ADR-0001). No bespoke methods.

    Implementations live under
    ``synthorg.persistence.{sqlite,postgres}.principle_override_repo``
    and are exposed on :class:`PersistenceBackend` for service-layer
    access.
    """

    @override
    async def save(self, entity: PrincipleOverride) -> None:
        """Insert or update the override at ``scope``.

        Args:
            entity: The principle override to persist.

        Raises:
            PersistenceError: If the write fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> PrincipleOverride | None:
        """Retrieve the override at ``scope``.

        Args:
            entity_id: The principle scope identifier.

        Returns:
            The override if present, ``None`` otherwise.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Remove the override at ``scope``.

        Args:
            entity_id: The principle scope identifier.

        Returns:
            ``True`` if a row was removed, ``False`` if no override existed.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        """List all overrides, ordered by ``scope`` ascending.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Overrides in ascending ``scope`` order.
        """
        ...
