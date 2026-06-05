"""Charter CRUD mixin for the interview service.

Read / list / edit / cancel operations on persisted
:class:`ProjectCharter` records, with the ownership fence (foreign
charters surface as NotFound) applied on every read path. The
interview turn pipeline lives in ``service``; this mixin owns only the
post-draft lifecycle of a charter.
"""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock
from synthorg.core.enums import CharterStatus, ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.models import CharterEditArgs, ProjectCharter
from synthorg.meta.errors import (
    CharterNotEditableError,
    CharterNotFoundError,
    CharterStateInconsistentError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.charter import (
    CHARTER_OWNERSHIP_DENIED,
    CHARTER_STATE_INCONSISTENT,
    CHARTER_STATUS_TRANSITIONED,
)
from synthorg.persistence.charter_protocol import (
    CharterFilterSpec,
    CharterRepository,
)
from synthorg.persistence.conversation_protocol import ConversationRepository

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT: int = 50


class CharterCrudMixin:
    """Read / list / edit / cancel lifecycle for persisted charters.

    Relies on the concrete :class:`CharterInterviewService` to supply
    the charter / conversation repositories and the clock seam.
    """

    _charter_repo: CharterRepository
    _conversation_repo: ConversationRepository
    _clock: Clock

    async def get(
        self,
        charter_id: NotBlankStr,
        *,
        requested_by: NotBlankStr | None = None,
    ) -> ProjectCharter:
        """Return a charter by id.

        When ``requested_by`` is supplied, a charter created by a
        different actor is treated as unfound so the response cannot
        be used to probe a foreign charter's existence; the
        discriminating ids surface in the structured warning so
        operators can still see ownership-fence events in logs.

        Raises:
            CharterNotFoundError: When the id is unknown OR the
                requester is not the creator.

        Returns:
            ``ProjectCharter`` instance.
        """
        charter = await self._charter_repo.get(charter_id)
        if charter is None:
            raise CharterNotFoundError(charter_id=charter_id)
        if requested_by is not None and charter.created_by != requested_by:
            logger.warning(
                CHARTER_OWNERSHIP_DENIED,
                charter_id=charter_id,
                created_by=charter.created_by,
                requested_by=requested_by,
            )
            raise CharterNotFoundError(charter_id=charter_id)
        return charter

    async def list_charters(
        self,
        *,
        status: CharterStatus | None = None,
        project_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        limit: int = _DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """List charters matching the optional filters, newest-first.

        Returns:
            Tuple of the declared element types.
        """
        return await self._charter_repo.query(
            CharterFilterSpec(
                status=status, project_id=project_id, created_by=created_by
            ),
            limit=limit,
            offset=offset,
        )

    async def edit_charter(
        self,
        charter_id: NotBlankStr,
        args: CharterEditArgs,
        *,
        edited_by: NotBlankStr,
    ) -> ProjectCharter:
        """Apply an in-place edit to a DRAFTED charter.

        Raises:
            CharterNotFoundError: When the id is unknown OR the editor
                is not the charter's creator (ownership fence shaped
                as NotFound so the response cannot probe existence).
            CharterNotEditableError: When the charter is no longer
                DRAFTED.

        Returns:
            ``ProjectCharter`` instance.
        """
        charter = await self.get(charter_id, requested_by=edited_by)
        if charter.status is not CharterStatus.DRAFTED:
            raise CharterNotEditableError(charter_id=charter_id)
        updates = self._edit_updates(args)
        updated = charter.model_copy(
            update={
                **updates,
                "version": charter.version + 1,
                "updated_at": self._clock.now(),
            }
        )
        await self._charter_repo.save(updated)
        logger.info(
            CHARTER_STATUS_TRANSITIONED,
            charter_id=charter_id,
            edited_by=edited_by,
            version=updated.version,
        )
        return updated

    @staticmethod
    def _edit_updates(args: CharterEditArgs) -> dict[str, object]:
        """Collect the provided (non-``None``) edit fields.

        Returns:
            Mapping with the declared key/value types.
        """
        candidates: dict[str, object | None] = {
            "title": args.title,
            "brief": args.brief,
            "goals": args.goals,
            "constraints": args.constraints,
            "success_criteria": args.success_criteria,
            "scope": args.scope,
            "envelope": args.envelope,
        }
        return {key: value for key, value in candidates.items() if value is not None}

    async def cancel_charter(
        self,
        charter_id: NotBlankStr,
        *,
        cancelled_by: NotBlankStr,
        enforce_ownership: bool = True,
    ) -> ProjectCharter:
        """Cancel a DRAFTED charter (terminal).

        ``enforce_ownership=False`` is reserved for admin paths (the MCP
        cancel handler is admin-gated at the registry layer) where an
        operator legitimately cancels a stalled charter they did not
        create.

        Returns:
            ``ProjectCharter`` reflecting the cancelled state.

        Raises:
            CharterNotFoundError: When the id is unknown OR (when
                ``enforce_ownership`` is set) the canceller is not the
                creator.
            CharterNotEditableError: When the charter is not DRAFTED.
            CharterStateInconsistentError: When the persisted state
                fails the post-cancel invariant check.
        """
        charter = await self.get(
            charter_id, requested_by=cancelled_by if enforce_ownership else None
        )
        if charter.status is not CharterStatus.DRAFTED:
            raise CharterNotEditableError(charter_id=charter_id)
        now = self._clock.now()
        transitioned = await self._charter_repo.transition_if(
            charter_id,
            from_state=CharterStatus.DRAFTED,
            to_state=CharterStatus.CANCELLED,
            updated_at=now,
        )
        if not transitioned:
            raise CharterNotEditableError(charter_id=charter_id)
        await self._close_conversation(charter.conversation_id, now)
        logger.info(
            CHARTER_STATUS_TRANSITIONED,
            charter_id=charter_id,
            cancelled_by=cancelled_by,
            from_state=CharterStatus.DRAFTED.value,
            to_state=CharterStatus.CANCELLED.value,
        )
        refreshed = await self._charter_repo.get(charter_id)
        if refreshed is None:
            # ``transition_if`` returned ``True``, so the row must exist
            # post-cancellation. Returning ``charter`` would surface a
            # stale ``DRAFTED`` status to the caller; record the
            # inconsistency before raising so operators see the missing
            # row in logs even though the exception bubbles past.
            logger.error(
                CHARTER_STATE_INCONSISTENT,
                charter_id=charter_id,
                stage="cancel_charter",
                pre_transition_status=charter.status.value,
                refreshed=None,
            )
            raise CharterStateInconsistentError(charter_id=charter_id)
        return refreshed

    async def _close_conversation(
        self, conversation_id: NotBlankStr, now: datetime
    ) -> None:
        """Best-effort close of the interview conversation (idempotent)."""
        await self._conversation_repo.transition_if(
            conversation_id,
            from_state=ConversationStatus.ACTIVE,
            to_state=ConversationStatus.CLOSED,
            updated_at=now.isoformat(),
        )
