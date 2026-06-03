"""In-memory store for human quality score overrides.

Stores at most one active override per agent. Handles expiration
by checking ``expires_at`` at query time.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger
from synthorg.observability.events.performance import (
    PERF_QUALITY_OVERRIDE_CLEARED,
    PERF_QUALITY_OVERRIDE_EXPIRED,
    PERF_QUALITY_OVERRIDE_SET,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import QualityOverride

logger = get_logger(__name__)


_DEFAULT_MAX_OVERRIDES: Final[int] = 10_000


class QualityOverrideStore:
    """In-memory store for human quality score overrides.

    Maintains at most one override per agent. Expiration is checked
    at query time -- expired overrides are not returned by
    :meth:`get_active_override`.

    Args:
        max_overrides: Maximum number of overrides stored. Prevents
            unbounded memory growth. Defaults to 10,000.
    """

    def __init__(
        self,
        *,
        max_overrides: int = _DEFAULT_MAX_OVERRIDES,
    ) -> None:
        if max_overrides < 1:
            msg = f"max_overrides must be >= 1, got {max_overrides}"
            raise ValueError(msg)
        self._overrides: dict[str, QualityOverride] = {}
        self._max_overrides = max_overrides

    def set_override(self, override: QualityOverride) -> None:
        """Set or replace the override for an agent.

        If a prior override (active or expired) exists for the agent,
        it is discarded and replaced.

        Args:
            override: The override to store.

        Raises:
            ValueError: If the store has reached ``max_overrides``
                capacity and this is a new agent. Replacements for
                existing agents always succeed.
        """
        agent_key = str(override.agent_id)
        if (
            agent_key not in self._overrides
            and len(self._overrides) >= self._max_overrides
        ):
            # Sweep expired entries before failing.
            now = datetime.now(UTC)
            expired_keys = [
                k
                for k, v in self._overrides.items()
                if v.expires_at is not None and v.expires_at <= now
            ]
            for ek in expired_keys:
                expired = self._overrides.pop(ek)
                logger.info(
                    PERF_QUALITY_OVERRIDE_EXPIRED,
                    agent_id=expired.agent_id,
                    expired_at=str(expired.expires_at),
                )
            if len(self._overrides) >= self._max_overrides:
                msg = f"Override store capacity reached ({self._max_overrides})"
                raise ValueError(msg)
        self._overrides[agent_key] = override
        logger.info(
            PERF_QUALITY_OVERRIDE_SET,
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
    ) -> QualityOverride | None:
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
                PERF_QUALITY_OVERRIDE_EXPIRED,
                agent_id=agent_id,
                expired_at=str(override.expires_at),
            )
            self._overrides.pop(str(agent_id), None)
            return None

        return override

    def clear_override(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Remove the active (non-expired) override for an agent.

        Expired overrides are evicted (logged at INFO) and not
        counted as a successful clear.

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
                PERF_QUALITY_OVERRIDE_EXPIRED,
                agent_id=agent_id,
                expired_at=str(override.expires_at),
            )
            self._overrides.pop(agent_key, None)
            return False

        self._overrides.pop(agent_key, None)
        logger.info(
            PERF_QUALITY_OVERRIDE_CLEARED,
            agent_id=agent_id,
        )
        return True

    def list_overrides(
        self,
        *,
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> tuple[QualityOverride, ...]:
        """List all overrides, optionally including expired ones.

        When ``include_expired=True``, returns all overrides still in
        the internal dict. Entries previously evicted by
        :meth:`get_active_override` or :meth:`clear_override` are not
        included -- this method only covers un-evicted entries.

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
