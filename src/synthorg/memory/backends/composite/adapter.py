"""Composite memory backend -- namespace-based routing.

Dispatches each memory operation to a child backend based on the
``namespace`` field of the request/query.  Memory IDs are prefixed
with the backend name (``"mem0:abc123"``) so that ``get()`` and
``delete()`` can route in O(1) without scanning all children.
"""

import asyncio
from types import MappingProxyType

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.composite.config import (
    CompositeBackendConfig,
)
from synthorg.memory.errors import (
    MemoryConfigError,
    MemoryConnectionError,
    MemoryRetrievalError,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONNECTED,
    MEMORY_BACKEND_CONNECTING,
    MEMORY_BACKEND_DISCONNECTED,
    MEMORY_BACKEND_DISCONNECTING,
    MEMORY_BACKEND_HEALTH_CHECK,
    MEMORY_BACKEND_NOT_CONNECTED,
    MEMORY_COMPOSITE_FANOUT_COMPLETE,
    MEMORY_COMPOSITE_FANOUT_PARTIAL,
    MEMORY_COMPOSITE_FANOUT_START,
    MEMORY_COMPOSITE_ID_RESOLVED,
    MEMORY_COMPOSITE_ROUTED,
)

logger = get_logger(__name__)

_ID_SEP = ":"


class CompositeBackend:
    """Namespace-based routing backend.

    Implements ``MemoryBackend`` and ``MemoryCapabilities`` protocols
    by delegating to child backends based on the ``namespace`` field.

    Args:
        children: Named backend instances (e.g. ``{"mem0": ...,
            "inmemory": ...}``).
        config: Routing configuration.

    Raises:
        MemoryConfigError: If a route references an unknown child.
    """

    def __init__(
        self,
        *,
        children: dict[str, MemoryBackend],
        config: CompositeBackendConfig,
    ) -> None:
        self._config = config
        self._children = MappingProxyType(dict(children))
        # Resolve namespace -> backend instance.
        namespace_to_backend: dict[str, MemoryBackend] = {}
        for ns, backend_name in config.routes.items():
            if backend_name not in children:
                msg = (
                    f"Composite route '{ns}' references unknown "
                    f"backend '{backend_name}'"
                )
                raise MemoryConfigError(msg)
            namespace_to_backend[ns] = children[backend_name]
        self._namespace_to_backend = MappingProxyType(namespace_to_backend)
        # Default backend.
        if config.default not in children:
            msg = f"Composite default references unknown backend '{config.default}'"
            raise MemoryConfigError(msg)
        self._default_backend = children[config.default]
        # Reverse map: id(backend) -> name (for ID prefixing).
        self._backend_to_name = MappingProxyType(
            {id(b): name for name, b in children.items()},
        )
        # Deduplicated backends for lifecycle operations.
        seen: dict[int, MemoryBackend] = {}
        for b in children.values():
            seen.setdefault(id(b), b)
        self._unique_backends = tuple(seen.values())

    # -- Routing helpers ----------------------------------------------

    def _resolve(self, namespace: str) -> MemoryBackend:
        """Resolve a namespace to its child backend.

        Returns:
            Result of type ``MemoryBackend``.
        """
        return self._namespace_to_backend.get(
            namespace,
            self._default_backend,
        )

    def _prefix_id(
        self,
        backend: MemoryBackend,
        raw_id: str,
    ) -> NotBlankStr:
        """Wrap a raw ID with the backend name prefix.

        Returns:
            Result of type ``NotBlankStr``.
        """
        name = self._backend_to_name[id(backend)]
        return NotBlankStr(f"{name}{_ID_SEP}{raw_id}")

    def _parse_id(
        self,
        prefixed_id: str,
    ) -> tuple[MemoryBackend, str]:
        """Split a prefixed ID into (backend, raw_id).

        Returns:
            Tuple ``(MemoryBackend, str)``.

        Raises:
            MemoryRetrievalError: If the prefix is unknown.
        """
        sep_idx = prefixed_id.find(_ID_SEP)
        if sep_idx < 0:
            msg = f"Memory ID missing backend prefix: {prefixed_id!r}"
            raise MemoryRetrievalError(msg)
        name = prefixed_id[:sep_idx]
        raw_id = prefixed_id[sep_idx + 1 :]
        backend = self._children.get(name)
        if backend is None:
            msg = f"Unknown backend prefix '{name}' in memory ID {prefixed_id!r}"
            raise MemoryRetrievalError(msg)
        logger.debug(
            MEMORY_COMPOSITE_ID_RESOLVED,
            backend=name,
            memory_id=prefixed_id,
        )
        return backend, raw_id

    def _rewrite_entry(
        self,
        backend: MemoryBackend,
        entry: MemoryEntry,
    ) -> MemoryEntry:
        """Return *entry* with a prefixed ID.

        Returns:
            Result of type ``MemoryEntry``.
        """
        return entry.model_copy(
            update={"id": self._prefix_id(backend, entry.id)},
        )

    # -- Lifecycle ----------------------------------------------------

    async def connect(self) -> None:
        """Connect all child backends."""
        logger.info(MEMORY_BACKEND_CONNECTING, backend="composite")
        async with asyncio.TaskGroup() as tg:
            for b in self._unique_backends:
                _ = tg.create_task(b.connect())
        logger.info(MEMORY_BACKEND_CONNECTED, backend="composite")

    async def disconnect(self) -> None:
        """Disconnect all child backends."""
        logger.info(MEMORY_BACKEND_DISCONNECTING, backend="composite")
        async with asyncio.TaskGroup() as tg:
            for b in self._unique_backends:
                _ = tg.create_task(b.disconnect())
        logger.info(MEMORY_BACKEND_DISCONNECTED, backend="composite")

    async def health_check(self) -> bool:
        """All children must be healthy.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        results: list[bool] = []
        async with asyncio.TaskGroup() as tg:
            for b in self._unique_backends:

                async def _check(
                    backend: MemoryBackend = b,
                ) -> None:
                    """Append the backend's health-check outcome to ``results``."""
                    results.append(await backend.health_check())

                _ = tg.create_task(_check())
        healthy = all(results)
        logger.debug(
            MEMORY_BACKEND_HEALTH_CHECK,
            backend="composite",
            healthy=healthy,
            children=len(results),
        )
        return healthy

    @property
    def is_connected(self) -> bool:
        """All children must be connected."""
        return all(b.is_connected for b in self._unique_backends)

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("composite")

    # -- Capabilities -------------------------------------------------

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        """Union of child capabilities."""
        cats: set[MemoryCategory] = set()
        for b in self._unique_backends:
            if hasattr(b, "supported_categories"):
                cats.update(b.supported_categories)
        return frozenset(cats) if cats else frozenset(MemoryCategory)

    @property
    def supports_graph(self) -> bool:
        """True if any child supports graph."""
        return any(getattr(b, "supports_graph", False) for b in self._unique_backends)

    @property
    def supports_temporal(self) -> bool:
        """True if all children support temporal.

        Backends missing the attribute default to True.
        """
        return all(getattr(b, "supports_temporal", True) for b in self._unique_backends)

    @property
    def supports_vector_search(self) -> bool:
        """True if any child supports vector search."""
        return any(
            getattr(b, "supports_vector_search", False) for b in self._unique_backends
        )

    @property
    def supports_shared_access(self) -> bool:
        """True if any child supports shared access."""
        return any(
            getattr(b, "supports_shared_access", False) for b in self._unique_backends
        )

    @property
    def max_memories_per_agent(self) -> int | None:
        """Minimum across children (most restrictive)."""
        limits = [
            getattr(b, "max_memories_per_agent", None) for b in self._unique_backends
        ]
        finite = [lim for lim in limits if lim is not None]
        return min(finite) if finite else None

    # -- CRUD ---------------------------------------------------------

    def _require_connected(self) -> None:
        """Require connected.

        Raises:
            MemoryConnectionError: If the related operation fails.
        """
        if not self.is_connected:
            msg = "CompositeBackend is not connected"
            logger.warning(
                MEMORY_BACKEND_NOT_CONNECTED,
                backend="composite",
            )
            raise MemoryConnectionError(msg)

    async def store(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Route store to the backend for ``request.namespace``.

        Returns:
            Result of type ``NotBlankStr``.
        """
        self._require_connected()
        backend = self._resolve(request.namespace)
        logger.debug(
            MEMORY_COMPOSITE_ROUTED,
            operation="store",
            namespace=request.namespace,
            backend=self._backend_to_name[id(backend)],
        )
        raw_id = await backend.store(agent_id, request)
        return self._prefix_id(backend, raw_id)

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Fan out to backends matching ``query.namespaces``.

        Returns:
            Tuple of ``MemoryEntry``.
        """
        self._require_connected()
        targets = self._resolve_retrieve_targets(query)
        logger.debug(
            MEMORY_COMPOSITE_FANOUT_START,
            operation="retrieve",
            target_count=len(targets),
        )
        all_entries = await self._fanout_retrieve(
            agent_id,
            query,
            targets,
        )
        all_entries.sort(
            key=lambda e: e.relevance_score if e.relevance_score is not None else 0.0,
            reverse=True,
        )
        return tuple(all_entries[: query.limit])

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> MemoryEntry | None:
        """Parse prefixed ID and delegate to the owning backend.

        Returns:
            The matching ``MemoryEntry``, or ``None`` when no match is found.
        """
        self._require_connected()
        backend, raw_id = self._parse_id(memory_id)
        entry = await backend.get(agent_id, NotBlankStr(raw_id))
        if entry is not None:
            return self._rewrite_entry(backend, entry)
        return None

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Parse prefixed ID and delegate to the owning backend.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        self._require_connected()
        backend, raw_id = self._parse_id(memory_id)
        return await backend.delete(agent_id, NotBlankStr(raw_id))

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Sum counts across all unique backends.

        Returns:
            Result of type ``int``.
        """
        self._require_connected()
        if len(self._unique_backends) == 1:
            return await self._unique_backends[0].count(
                agent_id,
                category=category,
            )
        totals: list[int] = []
        async with asyncio.TaskGroup() as tg:
            for b in self._unique_backends:

                async def _cnt(
                    backend: MemoryBackend = b,
                ) -> None:
                    """Append the backend's ``agent_id`` entry count to ``totals``."""
                    totals.append(
                        await backend.count(
                            agent_id,
                            category=category,
                        ),
                    )

                _ = tg.create_task(_cnt())
        return sum(totals)

    # -- Fan-out helpers (private) ------------------------------------

    def _resolve_retrieve_targets(
        self,
        query: MemoryQuery,
    ) -> list[MemoryBackend]:
        """Determine which backends to query for a retrieve call.

        Returns:
            List of ``MemoryBackend``.
        """
        if not query.namespaces:
            return list(self._unique_backends)
        seen: dict[int, MemoryBackend] = {}
        for ns in query.namespaces:
            b = self._resolve(ns)
            seen.setdefault(id(b), b)
        return list(seen.values())

    async def _fanout_retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
        targets: list[MemoryBackend],
    ) -> list[MemoryEntry]:
        """Fan out retrieve to *targets* with graceful degradation.

        Returns:
            List of ``MemoryEntry``.

        Raises:
            MemoryRetrievalError: If the related operation fails.
        """
        if len(targets) == 1:
            entries = await targets[0].retrieve(agent_id, query)
            return [self._rewrite_entry(targets[0], e) for e in entries]
        results: list[MemoryEntry] = []
        errors: list[str] = []
        async with asyncio.TaskGroup() as tg:
            for b in targets:

                async def _fetch(
                    backend: MemoryBackend = b,
                ) -> None:
                    """Retrieve from one backend and merge the rewritten entries.

                    Logs and records the error in ``errors`` on failure so one
                    slow or broken backend cannot abort the whole fan-out.
                    """
                    try:
                        entries = await backend.retrieve(
                            agent_id,
                            query,
                        )
                        results.extend(self._rewrite_entry(backend, e) for e in entries)
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        name = self._backend_to_name.get(
                            id(backend),
                            "?",
                        )
                        # The ``errors`` list flows into the
                        # aggregate ``MemoryRetrievalError`` and out
                        # to higher layers; scrub the exception text
                        # so backend connection strings don't leak
                        # via the aggregate.
                        errors.append(f"{name}: {safe_error_description(exc)}")
                        logger.warning(
                            MEMORY_COMPOSITE_FANOUT_PARTIAL,
                            operation="retrieve",
                            backend=name,
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                        )

                _ = tg.create_task(_fetch())
        if errors:
            if not results:
                msg = f"All backends failed during retrieve: {'; '.join(errors)}"
                raise MemoryRetrievalError(msg)
            logger.warning(
                MEMORY_COMPOSITE_FANOUT_PARTIAL,
                operation="retrieve",
                failed_backends=errors,
                successful_count=len(results),
            )
        else:
            logger.debug(
                MEMORY_COMPOSITE_FANOUT_COMPLETE,
                operation="retrieve",
                total=len(results),
            )
        return results
