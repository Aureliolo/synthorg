"""In-memory store for human collaboration score overrides.

Stores at most one active override per agent. Handles expiration
by checking ``expires_at`` at query time.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.performance import (
    PERF_OVERRIDE_CLEARED,
    PERF_OVERRIDE_EXPIRED,
    PERF_OVERRIDE_SET,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import CollaborationOverride

logger = get_logger(__name__)


class CollaborationOverrideStore:
    """In-memory store for human collaboration score overrides.

    Maintains at most one override per agent. Expiration is checked
    at query time -- expired overrides are not returned by
    :meth:`get_active_override`.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, CollaborationOverride] = {}

    def set_override(self, override: CollaborationOverride) -> None:
        """Set or replace the override for an agent.

        Args:
            override: The override to store.
        """
        agent_key = str(override.agent_id)
        self._overrides[agent_key] = override
        logger.info(
            PERF_OVERRIDE_SET,
            agent_id=override.agent_id,
            score=override.score,
            applied_by=override.applied_by,
            expires_at=str(override.expires_at) if override.expires_at else None,
        )

    def get_active_override(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> CollaborationOverride | None:
        """Get the active (non-expired) override for an agent.

        Args:
            agent_id: Agent to look up.
            now: Reference time for expiration check (defaults to UTC now).

        Returns:
            The active override, or ``None`` if absent or expired.
        """
        override = self._overrides.get(str(agent_id))
        if override is None:
            return None

        if now is None:
            now = datetime.now(UTC)

        if override.expires_at is not None and override.expires_at <= now:
            logger.info(
                PERF_OVERRIDE_EXPIRED,
                agent_id=agent_id,
                expired_at=str(override.expires_at),
            )
            del self._overrides[str(agent_id)]
            return None

        return override

    def clear_override(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Remove the active (non-expired) override for an agent.

        Expired overrides are silently evicted and not counted as
        a successful clear.

        Args:
            agent_id: Agent whose override to remove.
            now: Reference time for expiration check (defaults to UTC now).

        Returns:
            ``True`` if an active override was removed, ``False``
            if absent or already expired.
        """
        agent_key = str(agent_id)
        override = self._overrides.get(agent_key)
        if override is None:
            return False

        if now is None:
            now = datetime.now(UTC)

        if override.expires_at is not None and override.expires_at <= now:
            logger.info(
                PERF_OVERRIDE_EXPIRED,
                agent_id=agent_id,
                expired_at=str(override.expires_at),
            )
            del self._overrides[agent_key]
            return False

        del self._overrides[agent_key]
        logger.info(
            PERF_OVERRIDE_CLEARED,
            agent_id=agent_id,
        )
        return True

    def list_overrides(
        self,
        *,
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> tuple[CollaborationOverride, ...]:
        """List all overrides, optionally including expired ones.

        Args:
            include_expired: Whether to include expired overrides.
            now: Reference time for expiration check (defaults to UTC now).

        Returns:
            Tuple of overrides matching the filter.
        """
        if include_expired:
            return tuple(self._overrides.values())

        if now is None:
            now = datetime.now(UTC)

        return tuple(
            o
            for o in self._overrides.values()
            if o.expires_at is None or o.expires_at > now
        )
