"""Principle-override repository protocol and domain model.

The rollback executor's ``PromptMutator`` persists restored principle
text through this repository so the YAML-pack-loaded principles can be
overlaid at runtime. The store is keyed by ``scope`` (the principle
identifier from the YAML packs) and carries a ``restored_from`` field
recording the rollback operation that produced it for forensic audit.
"""

from datetime import datetime  # noqa: TC003 -- runtime default in protocol signature
from typing import Final, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001

_DEFAULT_LIST_LIMIT_100: Final[int] = 100


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
class PrincipleOverrideRepository(Protocol):
    """CRUD interface for principle overrides.

    Implementations live under
    ``synthorg.persistence.{sqlite,postgres}.principle_override_repo``
    and are exposed on :class:`PersistenceBackend` for service-layer
    access.
    """

    async def save(
        self,
        scope: NotBlankStr,
        text: NotBlankStr,
        *,
        restored_from: NotBlankStr,
        now: datetime | None = None,
    ) -> None:
        """Insert or update the override at ``scope``.

        Args:
            scope: Principle scope identifier.
            text: Override text.
            restored_from: Provenance of the override.
            now: Optional clock injection for tests (defaults to UTC now).

        Raises:
            PersistenceError: If the write fails.
        """
        ...

    async def get(self, scope: NotBlankStr) -> PrincipleOverride | None:
        """Retrieve the override at ``scope``.

        Returns:
            The override if present, ``None`` otherwise.
        """
        ...

    async def delete(self, scope: NotBlankStr) -> bool:
        """Remove the override at ``scope``.

        Returns:
            ``True`` if a row was removed, ``False`` if no override existed.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_100,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        """List all overrides, ordered by ``scope`` ascending."""
        ...
