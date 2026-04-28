"""In-memory user presence tracker for WebSocket connections.

Tracks which users are currently connected via WebSocket,
supporting multi-tab scenarios (multiple connections per user).
In-memory only -- presence is inherently ephemeral.
"""

from synthorg.observability import get_logger
from synthorg.observability.metrics_hub import record_client_disconnect

logger = get_logger(__name__)


class UserPresence:
    """Track online users via WebSocket connection counts.

    Each ``connect()`` increments a per-user counter; each
    ``disconnect()`` decrements it.  A user is online when
    their count is positive (at least one open tab/connection).

    Thread safety: not required -- all calls happen on the
    single-threaded asyncio event loop.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def connect(self, user_id: str) -> None:
        """Record a new WebSocket connection for a user.

        Args:
            user_id: The connecting user's ID.
        """
        self._counts[user_id] = self._counts.get(user_id, 0) + 1
        logger.debug(
            "user.presence.connect",
            user_id=user_id,
            count=self._counts[user_id],
        )

    def disconnect(self, user_id: str) -> None:
        """Record a WebSocket disconnection for a user.

        Args:
            user_id: The disconnecting user's ID.
        """
        count = self._counts.get(user_id, 0) - 1
        if count <= 0:
            self._counts.pop(user_id, None)
            logger.debug(
                "user.presence.disconnect",
                user_id=user_id,
                count=0,
            )
        else:
            self._counts[user_id] = count
            logger.debug(
                "user.presence.disconnect",
                user_id=user_id,
                count=count,
            )
        record_client_disconnect(
            transport="websocket",
            reason="client_initiated",
        )

    def is_online(self, user_id: str) -> bool:
        """Check whether a user has at least one open connection.

        Args:
            user_id: The user's ID.

        Returns:
            ``True`` if the user has one or more active connections.
        """
        return self._counts.get(user_id, 0) > 0

    def online_users(self) -> frozenset[str]:
        """Return the set of currently online user IDs.

        Returns:
            Frozen set of user IDs with active connections.
        """
        return frozenset(self._counts)

    def connection_count(self, user_id: str) -> int:
        """Return the number of active connections for a user.

        Args:
            user_id: The user's ID.

        Returns:
            Number of active WebSocket connections (0 if offline).
        """
        return self._counts.get(user_id, 0)
