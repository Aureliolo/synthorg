"""Compare-and-set retry loop for optimistic-concurrency mutations.

Centralizes the read-modify-write cycle that mutation services run
under optimistic concurrency.  Callers provide a ``read`` closure that
performs the read + validation + new-value construction (returning a
``(new_value, version)`` pair) and a ``write`` callable that persists
the new value guarded by the version.  The handler retries up to
``max_attempts`` on :class:`VersionConflictError`, emitting structured
``API_CONCURRENCY_CONFLICT`` logs at DEBUG on each retry and at
WARNING on the final exhausted attempt before re-raising.

Replaces the inline ``for attempt in range(_MAX_CAS_ATTEMPTS)`` loops
that previously lived in every mutation method.
"""

from typing import TYPE_CHECKING, Final, TypeVar

from synthorg.core.domain_errors import VersionConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_CONCURRENCY_CONFLICT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

T = TypeVar("T")
V = TypeVar("V")

_DEFAULT_MAX_ATTEMPTS: Final[int] = 2  # lint-allow: magic-numbers -- bootstrap


class CASRetryHandler:
    """Run a read-modify-write cycle under optimistic concurrency.

    Args:
        resource: Label emitted on ``API_CONCURRENCY_CONFLICT`` log
            records so operators can correlate retries to a specific
            mutation surface.
        max_attempts: Total attempts including the first (``2`` ->
            one retry).  Must be ``>= 1``.
    """

    def __init__(
        self,
        *,
        resource: str,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {max_attempts}"
            raise ValueError(msg)
        self._resource = resource
        self._max_attempts = max_attempts

    @property
    def max_attempts(self) -> int:
        """Maximum attempts (read by callers that need the bound)."""
        return self._max_attempts

    @property
    def resource(self) -> str:
        """Resource label this handler tags retries with."""
        return self._resource

    async def execute(
        self,
        read: Callable[[], Awaitable[tuple[T, V]]],
        write: Callable[[T, V], Awaitable[None]],
    ) -> T:
        """Run ``read`` then ``write`` with CAS retry.

        ``read`` is called on every attempt and is expected to perform
        the load + validation + new-value construction, returning a
        ``(new_value, version)`` pair.  ``write`` persists ``new_value``
        guarded by ``version`` and raises :class:`VersionConflictError`
        when the persisted version differs.  Validation errors raised
        by ``read`` (``NotFoundError``, ``ConflictError``,
        ``ValidationError``) propagate immediately without retry.

        Returns the persisted value on success.

        Raises:
            VersionConflictError: When all attempts collide; the
                final exception is the one raised by ``write``.
        """
        for attempt in range(self._max_attempts):
            new_value, version = await read()
            try:
                await write(new_value, version)
            except VersionConflictError:
                if attempt == self._max_attempts - 1:
                    logger.warning(
                        API_CONCURRENCY_CONFLICT,
                        resource=self._resource,
                        attempts=self._max_attempts,
                    )
                    raise
                logger.debug(
                    API_CONCURRENCY_CONFLICT,
                    resource=self._resource,
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                )
                continue
            else:
                return new_value
        msg = "CASRetryHandler.execute exited the retry loop without returning"
        raise AssertionError(msg)
