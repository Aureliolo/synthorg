"""Repository protocol for authored tool blueprint persistence.

Concrete implementations live in backend modules
(``synthorg.persistence.sqlite.tool_blueprint_repo`` and
``synthorg.persistence.postgres.tool_blueprint_repo``). The toolsmith's
``DynamicToolRegistry`` holds a reference typed against this protocol so
the storage backend can be swapped without changing the registry.

Composes :class:`StatefulRepository` (blueprint lifecycle is a state
machine: PENDING -> VALIDATED -> ACTIVE -> RETIRED) and
:class:`FilteredQueryRepository` (ADR-0001). No bespoke methods beyond
the generic surface.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.models import (
    ToolBlueprint,
    ToolBlueprintState,
    ToolSandboxBackend,
)
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)

if TYPE_CHECKING:
    from typing_extensions import TypedDict

    class TransitionKwargs(TypedDict, total=False):
        """Typed kwargs for :meth:`DynamicToolRepository.transition_if`.

        ``validation`` is keyed off **presence**, not value, like every
        other field here:

        * **Omit** the key entirely to leave the ``validation`` column
          untouched (the row keeps whatever evidence it already carries).
        * Pass a :class:`ToolValidationResult` to stamp gate evidence
          atomically with the timestamp; backends serialise it to the
          column's native JSON shape. This is required when the target
          state implies ``validation IS NOT NULL`` at the DB layer
          (validated / active / retired), otherwise the lifecycle CHECK
          rejects the transition.
        * Pass ``None`` to **explicitly clear** the column to ``NULL``.
          Callers wanting to preserve existing evidence must omit the
          key, NOT pass ``None``.
        """

        validated_at: object
        activated_at: object
        retired_at: object
        validation: object


class ToolBlueprintFilterSpec(BaseModel):
    """Filter spec for ``DynamicToolRepository.query`` (ADR-0001).

    All fields optional; an empty spec matches every blueprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    state: ToolBlueprintState | None = Field(default=None)
    capability: NotBlankStr | None = Field(default=None)
    sandbox_backend: ToolSandboxBackend | None = Field(default=None)


@runtime_checkable
class DynamicToolRepository(
    StatefulRepository[ToolBlueprint, NotBlankStr, ToolBlueprintState],
    FilteredQueryRepository[ToolBlueprint, ToolBlueprintFilterSpec],
    Protocol,
):
    """CRUD + state-transition interface for authored tool blueprints.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). All methods are async; constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    async def save(self, entity: ToolBlueprint) -> None:
        """Upsert a blueprint.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> ToolBlueprint | None:
        """Retrieve a blueprint by id, or ``None`` if absent.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a blueprint by id; ``True`` iff a row existed.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ToolBlueprint, ...]:
        """List blueprints ordered by ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def query(
        self,
        filter_spec: ToolBlueprintFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ToolBlueprint, ...]:
        """List blueprints matching the filter spec (paginated).

        Ordered by ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def count(self, filter_spec: ToolBlueprintFilterSpec) -> int:
        """Count blueprints matching the filter spec.

        Raises:
            QueryError: If the query fails.
        """
        ...

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ToolBlueprintState,
        to_state: ToolBlueprintState,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for blueprint state transitions.

        Transitions ``entity_id`` from ``from_state`` to ``to_state``
        atomically. ``**updates`` carries status-correlated timestamp
        columns (``validated_at`` / ``activated_at`` / ``retired_at``);
        unknown keys are rejected at the implementation layer.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors, or if an unknown update key
                is supplied.
        """
        ...
