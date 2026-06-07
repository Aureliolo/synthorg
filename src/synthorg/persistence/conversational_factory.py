"""Backend-aware factory for the conversational persistence trio.

These repositories are deliberately NOT exposed as
``PersistenceBackend`` properties (mirroring the ``ApprovalRepository``
precedent): the conversational interface is opt-in and wires its own
store directly off the connected backend handle. Keeping the
concrete-backend imports here means the persistence boundary holds --
no ``api`` / ``meta`` module imports ``aiosqlite`` / ``psycopg``.
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.conversational import (
    PERSISTENCE_CONVERSATIONAL_HANDLE_UNAVAILABLE,
    PERSISTENCE_CONVERSATIONAL_UNKNOWN_BACKEND,
)

if TYPE_CHECKING:
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.conversation_invite_protocol import (
        ConversationInviteRepository,
    )
    from synthorg.persistence.conversation_participant_protocol import (
        ConversationParticipantRepository,
    )
    from synthorg.persistence.conversation_protocol import (
        ConversationRepository,
        ConversationTurnRepository,
    )
    from synthorg.persistence.conversational_proposal_protocol import (
        ConversationalProposalRepository,
    )
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

_SQLITE: str = "sqlite"
_POSTGRES: str = "postgres"


class ConversationalRepositories:
    """The durable stores the proposer, dispatcher + group chat need."""

    __slots__ = (
        "conversation_repo",
        "invite_repo",
        "participant_repo",
        "proposal_repo",
        "turn_repo",
    )

    def __init__(
        self,
        *,
        conversation_repo: ConversationRepository,
        turn_repo: ConversationTurnRepository,
        proposal_repo: ConversationalProposalRepository,
        participant_repo: ConversationParticipantRepository,
        invite_repo: ConversationInviteRepository,
    ) -> None:
        self.conversation_repo = conversation_repo
        self.turn_repo = turn_repo
        self.proposal_repo = proposal_repo
        self.participant_repo = participant_repo
        self.invite_repo = invite_repo


def build_conversational_repositories(
    backend: PersistenceBackend | None,
) -> ConversationalRepositories | None:
    """Construct the conversational repos for *backend*.

    Returns ``None`` when the backend is absent / not connected, or is
    an unknown variant, so the caller degrades to a 503 rather than
    raising during boot.

    Returns:
        The matching value, or ``None`` when absent.
    """
    if backend is None or not getattr(backend, "is_connected", False):
        return None
    name = backend.backend_name
    if name not in (_SQLITE, _POSTGRES):
        logger.warning(
            PERSISTENCE_CONVERSATIONAL_UNKNOWN_BACKEND,
            backend_name=name,
        )
        return None
    # The DB handle is acquired defensively: a degenerate backend
    # (e.g. an in-memory test fake whose ``get_db`` raises) must
    # degrade the conversational interface to "unavailable" -- a
    # clean 503 at the controller -- rather than crashing app
    # startup for every unrelated request path.
    try:
        handle = backend.get_db()
        write_context = backend.write_context
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PERSISTENCE_CONVERSATIONAL_HANDLE_UNAVAILABLE,
            backend_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if name == _SQLITE:
        from synthorg.persistence.sqlite.conversation_invite_repo import (  # noqa: PLC0415
            SQLiteConversationInviteRepository,
        )
        from synthorg.persistence.sqlite.conversation_participant_repo import (  # noqa: PLC0415
            SQLiteConversationParticipantRepository,
        )
        from synthorg.persistence.sqlite.conversation_repo import (  # noqa: PLC0415
            SQLiteConversationRepository,
            SQLiteConversationTurnRepository,
        )
        from synthorg.persistence.sqlite.conversational_proposal_repo import (  # noqa: PLC0415
            SQLiteConversationalProposalRepository,
        )

        sqlite_handle = cast("aiosqlite.Connection", handle)
        return ConversationalRepositories(
            conversation_repo=SQLiteConversationRepository(
                sqlite_handle, write_context=write_context
            ),
            turn_repo=SQLiteConversationTurnRepository(
                sqlite_handle, write_context=write_context
            ),
            proposal_repo=SQLiteConversationalProposalRepository(
                sqlite_handle, write_context=write_context
            ),
            participant_repo=SQLiteConversationParticipantRepository(
                sqlite_handle, write_context=write_context
            ),
            invite_repo=SQLiteConversationInviteRepository(
                sqlite_handle, write_context=write_context
            ),
        )
    from synthorg.persistence.postgres.conversation_invite_repo import (  # noqa: PLC0415
        PostgresConversationInviteRepository,
    )
    from synthorg.persistence.postgres.conversation_participant_repo import (  # noqa: PLC0415
        PostgresConversationParticipantRepository,
    )
    from synthorg.persistence.postgres.conversation_repo import (  # noqa: PLC0415
        PostgresConversationRepository,
        PostgresConversationTurnRepository,
    )
    from synthorg.persistence.postgres.conversational_proposal_repo import (  # noqa: PLC0415
        PostgresConversationalProposalRepository,
    )

    pg_handle = cast("AsyncConnectionPool", handle)
    return ConversationalRepositories(
        conversation_repo=PostgresConversationRepository(pg_handle),
        turn_repo=PostgresConversationTurnRepository(pg_handle),
        proposal_repo=PostgresConversationalProposalRepository(pg_handle),
        participant_repo=PostgresConversationParticipantRepository(pg_handle),
        invite_repo=PostgresConversationInviteRepository(pg_handle),
    )


__all__ = [
    "ConversationalRepositories",
    "build_conversational_repositories",
]
