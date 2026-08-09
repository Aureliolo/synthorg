"""Record what a deleted entity was, before it stops being there.

The records that reference a task keep its identifier after the task is
gone, which is what lets a cost row still say what it was for. That only
works while the identifier resolves to something, so a deletion writes a
tombstone naming the entity, the person who removed it and when.

Only a person's deletion is recorded here, because only a person's deletion
happens: nothing in the system removes a task, plan or project on its own.
The one place that could drift from that is this module, so the writing
lives here rather than being repeated at each call site.
"""

from datetime import UTC, datetime

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.deleted_entity import DeletedEntity, DeletedEntityKind
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PROJECT_CASCADE_CONTENDED
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

#: Stands in when an entity carried no usable name. A tombstone exists to
#: answer "what was this", and refusing to write one because the answer is
#: thin would lose the fact that it existed at all.
_UNNAMED: NotBlankStr = NotBlankStr("(unnamed)")


async def record_deletion_for(
    app_state: AppState,
    *,
    kind: DeletedEntityKind,
    entity_id: str,
    display_name: str | None,
    deleted_by: str,
) -> None:
    """Write the tombstone, resolving the store here rather than at the call.

    The deletion has already happened by the time any caller reaches this,
    so a backend that cannot be resolved is the same class of miss as a
    write that fails: worth a warning, never worth turning a completed
    delete into an error the operator has to interpret.

    Args:
        app_state: State the tombstone store is resolved from.
        kind: Whether a task, plan or project was removed.
        entity_id: The identifier surviving records still name.
        display_name: What it was called, when it had a name.
        deleted_by: The person who asked.
    """
    try:
        persistence = persistence_of(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the deletion is already decided
        reraise_critical(exc)
        logger.warning(
            API_PROJECT_CASCADE_CONTENDED,
            entity_kind=kind.value,
            entity_id=entity_id,
            note="no tombstone store; deletion not recorded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    await record_deletion(
        persistence,
        kind=kind,
        entity_id=entity_id,
        display_name=display_name,
        deleted_by=deleted_by,
    )


async def record_deletion(
    persistence: PersistenceBackend,
    *,
    kind: DeletedEntityKind,
    entity_id: str,
    display_name: str | None,
    deleted_by: str,
) -> None:
    """Write the tombstone for one deleted entity.

    Best-effort against the store, deliberately: the deletion the operator
    asked for has already been decided, and failing it because the note
    about it could not be filed would be the wrong way round. The failure
    is logged with the identifier so the gap is visible.

    Args:
        persistence: Backend holding the tombstone store.
        kind: Whether a task, plan or project was removed.
        entity_id: The identifier surviving records still name.
        display_name: What it was called, when it had a name.
        deleted_by: The person who asked. Never a system actor: nothing in
            the system deletes an entity on its own.
    """
    stripped = (display_name or "").strip()
    try:
        await persistence.deleted_entities.append(
            DeletedEntity(
                entity_kind=kind,
                entity_id=NotBlankStr(entity_id),
                display_name=NotBlankStr(stripped) if stripped else _UNNAMED,
                deleted_by=NotBlankStr(deleted_by),
                deleted_at=datetime.now(UTC),
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the deletion is already decided
        reraise_critical(exc)
        logger.warning(
            API_PROJECT_CASCADE_CONTENDED,
            entity_kind=kind.value,
            entity_id=entity_id,
            note="deletion tombstone not written",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["record_deletion", "record_deletion_for"]
