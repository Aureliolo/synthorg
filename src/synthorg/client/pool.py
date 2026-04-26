"""Client pool and pool selection strategies."""

import asyncio
import random

from synthorg.client.models import (
    ClientProfile,  # noqa: TC001
    PoolConstraints,  # noqa: TC001
)
from synthorg.client.protocols import ClientInterface  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ClientPool:
    """Thread-safe mutable collection of clients.

    Holds both ``ClientProfile`` metadata (exposed to the API) and
    the underlying ``ClientInterface`` instances (used by the
    simulation runner and review stages). Profiles and clients are
    keyed by ``client_id`` and kept in insertion order.
    """

    def __init__(self) -> None:
        """Initialize an empty pool."""
        self._lock = asyncio.Lock()
        self._clients: dict[str, ClientInterface] = {}
        self._profiles: dict[str, ClientProfile] = {}
        self._active: dict[str, bool] = {}

    async def add(
        self,
        *,
        profile: ClientProfile,
        client: ClientInterface,
    ) -> None:
        """Add or replace a client and its profile.

        Re-adding a previously deactivated client id resets the
        active flag to ``True``: deactivation is a soft delete,
        and ``add`` is the explicit "make this present and live"
        operation.

        Args:
            profile: Profile describing the client.
            client: Client implementation keyed on the profile id.
        """
        async with self._lock:
            self._profiles[profile.client_id] = profile
            self._clients[profile.client_id] = client
            self._active[profile.client_id] = True

    async def has_profile(self, client_id: str) -> bool:
        """Return whether a profile exists for ``client_id``.

        Includes deactivated profiles -- mirrors ``get_profile`` which
        is also index-inclusive. Use ``is_active`` to distinguish
        live clients from tombstoned ones.
        """
        async with self._lock:
            return client_id in self._profiles

    async def remove(self, client_id: str) -> ClientProfile:
        """Remove a client by id and return its profile.

        Raises:
            KeyError: If the client id is not known.
        """
        async with self._lock:
            if client_id not in self._profiles:
                msg = f"Client {client_id!r} not found"
                raise KeyError(msg)
            profile = self._profiles.pop(client_id)
            self._clients.pop(client_id, None)
            self._active.pop(client_id, None)
            return profile

    async def deactivate(self, client_id: str) -> ClientProfile:
        """Mark a client inactive without removing it from the pool.

        Inactive clients remain visible to ``get_profile`` but are
        excluded from ``list_clients`` and ``list_profiles`` so the
        runner and review stages stop selecting them. Idempotent:
        deactivating an already-inactive client is a no-op.

        Raises:
            KeyError: If the client id is not known.
        """
        async with self._lock:
            if client_id not in self._profiles:
                msg = f"Client {client_id!r} not found"
                raise KeyError(msg)
            self._active[client_id] = False
            return self._profiles[client_id]

    async def reactivate(self, client_id: str) -> ClientProfile:
        """Re-enable a previously deactivated client.

        Idempotent: reactivating an already-active client is a
        no-op that returns the existing profile.

        Raises:
            KeyError: If the client id is not known.
        """
        async with self._lock:
            if client_id not in self._profiles:
                msg = f"Client {client_id!r} not found"
                raise KeyError(msg)
            self._active[client_id] = True
            return self._profiles[client_id]

    async def is_active(self, client_id: str) -> bool:
        """Return whether the client is currently active.

        Raises:
            KeyError: If the client id is not known.
        """
        async with self._lock:
            if client_id not in self._profiles:
                msg = f"Client {client_id!r} not found"
                raise KeyError(msg)
            return self._active.get(client_id, True)

    async def get_profile(self, client_id: str) -> ClientProfile:
        """Return the stored profile for ``client_id``.

        Raises:
            KeyError: If the client id is not known.
        """
        async with self._lock:
            if client_id not in self._profiles:
                msg = f"Client {client_id!r} not found"
                raise KeyError(msg)
            return self._profiles[client_id]

    async def list_profiles(
        self,
        *,
        include_inactive: bool = False,
    ) -> tuple[ClientProfile, ...]:
        """Return a snapshot tuple of stored profiles.

        Args:
            include_inactive: Include deactivated profiles. Defaults
                to excluding them so list endpoints show the "live"
                pool by default.
        """
        async with self._lock:
            if include_inactive:
                return tuple(self._profiles.values())
            return tuple(
                profile
                for cid, profile in self._profiles.items()
                if self._active.get(cid, True)
            )

    async def list_clients(self) -> tuple[ClientInterface, ...]:
        """Return active client instances only.

        Inactive clients are excluded so the simulation runner and
        review stages never select a deactivated persona.
        """
        async with self._lock:
            return tuple(
                client
                for cid, client in self._clients.items()
                if self._active.get(cid, True)
            )

    async def size(self) -> int:
        """Return the number of active clients in the pool."""
        async with self._lock:
            return sum(1 for cid in self._clients if self._active.get(cid, True))


def _client_profile(client: ClientInterface) -> ClientProfile | None:
    """Return the ``profile`` attribute if exposed, else ``None``."""
    return getattr(client, "profile", None)


def _filter_by_constraints(
    pool: tuple[ClientInterface, ...],
    constraints: PoolConstraints,
) -> list[ClientInterface]:
    """Filter clients by strictness and required-domains constraints."""
    results: list[ClientInterface] = []
    for client in pool:
        profile = _client_profile(client)
        if profile is None:
            continue
        if profile.strictness_level < constraints.min_strictness:
            continue
        if profile.strictness_level > constraints.max_strictness:
            continue
        if constraints.required_domains:
            expertise = set(profile.expertise_domains)
            if not set(constraints.required_domains).issubset(expertise):
                continue
        results.append(client)
    return results


class RoundRobinStrategy:
    """Cycles through clients in insertion order.

    State (the rotation cursor) is carried across calls, so
    repeated invocations on the same strategy instance continue
    the cycle.

    **Not thread-safe.** The cursor is updated without a lock, so
    do not share a single ``RoundRobinStrategy`` instance across
    concurrent ``select_clients`` invocations from different async
    tasks -- create one instance per simulation run or guard calls
    externally.
    """

    def __init__(self) -> None:
        """Initialize with an empty cursor."""
        self._cursor = 0

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        """Select up to ``constraints.max_clients`` via round-robin."""
        filtered = _filter_by_constraints(pool, constraints)
        if not filtered:
            return ()
        count = min(constraints.max_clients, len(filtered))
        selection: list[ClientInterface] = []
        for _ in range(count):
            selection.append(filtered[self._cursor % len(filtered)])
            self._cursor += 1
        return tuple(selection)


class WeightedRandomStrategy:
    """Weighted random selection by strictness level.

    Strict personas have higher selection probability. Useful for
    adversarial simulation runs that want more rigorous reviewers.

    **Not thread-safe.** The underlying ``random.Random`` instance
    is mutated on every call, so do not share a single
    ``WeightedRandomStrategy`` across concurrent ``select_clients``
    invocations -- create one instance per simulation run.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        """Initialize with an optional random seed."""
        self._rng = (
            random.Random(seed)  # noqa: S311
            if seed is not None
            else random.Random()  # noqa: S311
        )

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        """Sample up to ``constraints.max_clients`` weighted by strictness."""
        filtered = _filter_by_constraints(pool, constraints)
        if not filtered:
            return ()
        count = min(constraints.max_clients, len(filtered))
        weights = [
            max(0.01, _client_profile(client).strictness_level)  # type: ignore[union-attr]
            for client in filtered
        ]
        chosen: list[ClientInterface] = []
        available = list(filtered)
        available_weights = list(weights)
        for _ in range(count):
            if not available:
                break
            pick = self._rng.choices(available, weights=available_weights, k=1)[0]
            for i, client in enumerate(available):
                if client is pick:
                    chosen.append(pick)
                    available.pop(i)
                    available_weights.pop(i)
                    break
        return tuple(chosen)


class DomainMatchedStrategy:
    """Select clients matching all ``required_domains`` constraints.

    When no required domains are supplied, falls back to
    insertion-order selection of the first ``max_clients`` entries.
    """

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        """Select clients matching all required domains."""
        filtered = _filter_by_constraints(pool, constraints)
        if not filtered:
            return ()
        count = min(constraints.max_clients, len(filtered))
        return tuple(filtered[:count])
