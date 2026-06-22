# module-kind: complex_service
"""Agent registry service.

Hot-pluggable agent registry for tracking active agents,
their identities, and lifecycle status transitions (D8.3).

One cohesive responsibility: maintain the authoritative agent
identity registry. CRUD (register/unregister/get/list), identity
updates (status / generic-field / evolved-replacement /
apply-arbitrary-mutation), and the autonomy-mutation primitives
(``snapshot_current_autonomy_level``, ``apply_autonomy_level``, and
the atomic ``apply_autonomy_update_atomic``) all operate on the
same ``self._agents`` dict under ``self._lock``. The
autonomy-promotion workflow itself (request / approval / audit
row) is owned by :class:`synthorg.hr.autonomy_workflow.AutonomyWorkflow`;
the autonomy-mutation methods on this registry are the narrow
read / apply / read-and-apply hooks the workflow consumes so its
mutations serialise with the registry's own writes without
reaching into ``_agents`` directly.
"""

import asyncio
from typing import Final

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.clock import Clock
from synthorg.core.normalization import (
    compare_ci,
    find_by_name_ci,
    normalize_identifier,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_AGENT_STATUS_TRANSITIONED,
    HR_REGISTRY_AGENT_REGISTERED,
    HR_REGISTRY_AGENT_REMOVED,
    HR_REGISTRY_CLEARED,
    HR_REGISTRY_IDENTITY_EVOLVED,
    HR_REGISTRY_IDENTITY_UPDATED,
    HR_REGISTRY_STATUS_UPDATED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.observability.events.versioning import VERSION_SNAPSHOT_FAILED
from synthorg.security.autonomy.models import (
    AutonomyUpdate,
    AutonomyUpdateResult,
)
from synthorg.versioning.service import VersioningService

# Upper bound on a single ``get_by_names`` batch.  Caller inputs can
# originate from user-supplied request bodies (e.g. the coordination
# endpoint's ``agent_names``), so the batch must not block the
# registry's single ``asyncio.Lock`` for an unbounded period.  A
# well-formed organisation has far fewer active agents than this
# ceiling; anything larger is assumed to be misuse.
MAX_BATCH_NAMES_LOOKUP: Final[int] = 1024

logger = get_logger(__name__)


class AgentRegistryService:
    """Hot-pluggable agent registry.

    Coroutine-safe via asyncio.Lock within a single event loop.
    Stores agent identities keyed by agent ID (string form of UUID).
    """

    def __init__(
        self,
        versioning: VersioningService[AgentIdentity] | None = None,
    ) -> None:
        self._agents: dict[str, AgentIdentity] = {}
        self._lock = asyncio.Lock()
        self._versioning = versioning

    async def clear(self) -> None:
        """Reset all registered agents.

        Holds the same ``self._lock`` as ``register`` / ``unregister``
        / ``update_*`` so a concurrent caller cannot observe a partial
        clear -- the registry is either fully empty or in the state
        the contending writer claimed.

        Async test fixtures call ``await registry.clear()`` directly.
        The sync reset entry point lives in
        :mod:`synthorg.hr.registry_testing` (not on this class) so the
        lock-bypass cannot be invoked from production code by
        autocomplete. Sync pytest fixtures
        (``tests/unit/api/conftest.py``) call
        ``reset_registry_for_test_sync(registry)`` from that module.
        """
        async with self._lock:
            cleared_count = len(self._agents)
            self._agents.clear()
        logger.info(HR_REGISTRY_CLEARED, cleared_count=cleared_count)

    def bind_versioning(
        self,
        versioning: VersioningService[AgentIdentity],
    ) -> None:
        """Attach a versioning service after construction.

        Enables the app factory to construct the registry synchronously in
        ``create_app()`` and wire versioning later in ``on_startup()``, after
        the persistence backend is connected (its ``identity_versions``
        property requires ``connect()`` to have run).
        """
        self._versioning = versioning

    @property
    def has_versioning(self) -> bool:
        """Return ``True`` when a versioning service is attached.

        Public predicate used by the app factory's startup wiring so it
        doesn't need to read the private ``_versioning`` slot.
        """
        return self._versioning is not None

    async def register(
        self,
        identity: AgentIdentity,
        *,
        saved_by: str = "system",
    ) -> None:
        """Register a new agent.

        Args:
            identity: The agent identity to register.
            saved_by: Actor triggering the registration (recorded in
                version history).  Defaults to ``"system"``.

        Raises:
            AgentAlreadyRegisteredError: If the agent is already registered.
        """
        agent_key = str(identity.id)
        async with self._lock:
            if agent_key in self._agents:
                msg = f"Agent {identity.name!r} ({agent_key}) is already registered"
                logger.warning(
                    HR_REGISTRY_AGENT_REGISTERED,
                    agent_id=agent_key,
                    error=msg,
                )
                raise AgentAlreadyRegisteredError(msg)
            self._agents[agent_key] = identity

        logger.info(
            HR_REGISTRY_AGENT_REGISTERED,
            agent_id=agent_key,
            agent_name=str(identity.name),
            status=identity.status.value,
        )
        await self._snapshot(identity, saved_by=saved_by)

    async def unregister(self, agent_id: NotBlankStr) -> AgentIdentity:
        """Remove an agent from the registry.

        Args:
            agent_id: The agent identifier to remove.

        Returns:
            The removed agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
        """
        async with self._lock:
            identity = self._agents.pop(str(agent_id), None)
        if identity is None:
            msg = f"Agent {agent_id!r} not found in registry"
            logger.warning(
                HR_REGISTRY_AGENT_REMOVED,
                agent_id=str(agent_id),
                error=msg,
            )
            raise AgentNotFoundError(msg)

        logger.info(
            HR_REGISTRY_AGENT_REMOVED,
            agent_id=str(agent_id),
            agent_name=str(identity.name),
        )
        return identity

    async def get(self, agent_id: NotBlankStr) -> AgentIdentity | None:
        """Retrieve an agent identity by ID.

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent identity, or None if not found.
        """
        async with self._lock:
            return self._agents.get(str(agent_id))

    async def get_by_ids(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> dict[str, AgentIdentity]:
        """Batch lookup by id, acquiring the registry lock exactly once.

        Fanning out N separate ``get`` calls (the old pattern) requires N
        lock acquisitions serialised under the shared lock; this batch
        method reduces that to a single acquisition. Mirrors
        :meth:`get_by_names`.

        Args:
            agent_ids: Agent ids to resolve. Duplicates collapse to one
                entry in the result.

        Returns:
            Mapping of id string to identity for every id that resolves;
            ids with no registered agent are omitted (callers treat a
            missing key as "not found").

        Raises:
            ValueError: If ``len(agent_ids)`` exceeds
                ``MAX_BATCH_NAMES_LOOKUP``; the registry lock must not be
                held for an unbounded scan.
        """
        if not agent_ids:
            return {}
        if len(agent_ids) > MAX_BATCH_NAMES_LOOKUP:
            msg = (
                f"get_by_ids batch of {len(agent_ids)} exceeds "
                f"MAX_BATCH_NAMES_LOOKUP={MAX_BATCH_NAMES_LOOKUP}"
            )
            raise ValueError(msg)
        async with self._lock:
            resolved: dict[str, AgentIdentity] = {}
            for agent_id in agent_ids:
                identity = self._agents.get(str(agent_id))
                if identity is not None:
                    resolved[str(agent_id)] = identity
            return resolved

    async def get_by_name(self, name: NotBlankStr) -> AgentIdentity | None:
        """Retrieve an agent identity by name.

        Args:
            name: The agent name to search for.

        Returns:
            The first matching agent, or None.
        """
        async with self._lock:
            return find_by_name_ci(self._agents.values(), str(name))

    async def get_by_names(
        self,
        names: tuple[NotBlankStr, ...],
    ) -> tuple[AgentIdentity | None, ...]:
        """Batch lookup preserving input order with ``None`` for misses.

        Acquires the registry lock exactly once regardless of batch
        size.  Fanning out N separate ``get_by_name`` calls (the old
        pattern) required N lock acquisitions and serialised each
        lookup under a shared lock; this batch method reduces that to
        a single acquisition.

        Args:
            names: Ordered tuple of agent names to resolve
                (case-insensitive).

        Returns:
            Tuple of resolved identities in the same order as
            ``names``.  Each entry is the first matching agent or
            ``None`` if no agent has that name.  When multiple
            registered agents share the same name (case-insensitive),
            the first-registered identity wins, matching
            ``get_by_name`` semantics.

        Raises:
            ValueError: If ``len(names)`` exceeds
                ``MAX_BATCH_NAMES_LOOKUP``; the registry lock must not
                be held for an unbounded scan when callers forward
                user-supplied name lists.
        """
        if not names:
            return ()
        if len(names) > MAX_BATCH_NAMES_LOOKUP:
            msg = (
                f"get_by_names batch of {len(names)} exceeds "
                f"MAX_BATCH_NAMES_LOOKUP={MAX_BATCH_NAMES_LOOKUP}"
            )
            raise ValueError(msg)
        async with self._lock:
            by_normalised_name: dict[str, AgentIdentity] = {}
            for identity in self._agents.values():
                key = normalize_identifier(str(identity.name))
                # First registration wins on name collision, matching
                # ``get_by_name`` semantics (which routes through
                # ``find_by_name_ci`` -- casefold + whitespace strip).
                by_normalised_name.setdefault(key, identity)
            return tuple(
                by_normalised_name.get(normalize_identifier(str(name)))
                for name in names
            )

    async def list_active(self) -> tuple[AgentIdentity, ...]:
        """List all agents with ACTIVE status.

        Returns:
            Tuple of active agent identities.
        """
        async with self._lock:
            return tuple(
                a for a in self._agents.values() if a.status == AgentStatus.ACTIVE
            )

    def active_agent_ids(self) -> tuple[str, ...]:
        """Snapshot the ids of active agents synchronously (lock-free).

        Provides the synchronous agent-id source the performance signal
        aggregator's ``agent_ids_provider`` contract requires. The read
        is a best-effort point-in-time snapshot rather than a
        lock-guarded view: an id appearing or disappearing between turns
        only shifts which agents the aggregator queries on the next read,
        which is acceptable for an observability signal.

        Returns:
            The ids of the currently ACTIVE agents.
        """
        return tuple(
            str(a.id) for a in self._agents.values() if a.status == AgentStatus.ACTIVE
        )

    async def list_by_department(
        self,
        department: NotBlankStr,
    ) -> tuple[AgentIdentity, ...]:
        """List agents in a specific department.

        Args:
            department: Department name to filter by.

        Returns:
            Tuple of matching agent identities.
        """
        async with self._lock:
            return tuple(
                a
                for a in self._agents.values()
                if compare_ci(str(a.department), str(department))
            )

    async def update_status(
        self,
        agent_id: NotBlankStr,
        status: AgentStatus,
    ) -> AgentIdentity:
        """Update an agent's lifecycle status.

        Emits ``HR_AGENT_STATUS_TRANSITIONED`` AFTER the registry
        write succeeds, carrying ``from_status`` / ``to_status`` /
        ``agent_id`` so observers can audit every persisted hop on
        the agent lifecycle.  No-op transitions (status unchanged)
        skip the transition event but still log
        ``HR_REGISTRY_STATUS_UPDATED`` for the write itself.

        Args:
            agent_id: The agent identifier.
            status: New status.

        Returns:
            Updated agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
        """
        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_STATUS_UPDATED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            from_status = identity.status
            updated = identity.model_copy(update={"status": status})
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_STATUS_UPDATED,
            agent_id=key,
            status=status.value,
        )
        if from_status != status:
            logger.info(
                HR_AGENT_STATUS_TRANSITIONED,
                agent_id=key,
                from_status=from_status.value,
                to_status=status.value,
            )
        return updated

    # Allowlist of fields that may be updated via update_identity.
    # Only fields listed here are accepted; all others (authority,
    # status, tools.access_level, etc.) are rejected to prevent
    # mass assignment of security-sensitive fields.
    _UPDATABLE_FIELDS: frozenset[str] = frozenset({"level", "model"})

    async def update_identity(
        self,
        agent_id: NotBlankStr,
        **updates: object,
    ) -> AgentIdentity:
        """Update agent identity fields via model_copy(update=...).

        Only fields in ``_UPDATABLE_FIELDS`` are accepted.  Use
        ``update_status`` for status changes.

        Args:
            agent_id: The agent identifier.
            **updates: Fields to update on the AgentIdentity.

        Returns:
            Updated agent identity.

        Raises:
            AgentNotFoundError: If the agent is not found.
            ValueError: If any field is not in the allowlist.
        """
        disallowed = set(updates.keys()) - self._UPDATABLE_FIELDS
        if disallowed:
            msg = (
                f"Fields not allowed for update_identity: "
                f"{sorted(disallowed)}; allowed: {sorted(self._UPDATABLE_FIELDS)}"
            )
            logger.warning(
                HR_REGISTRY_IDENTITY_UPDATED,
                agent_id=str(agent_id),
                error=msg,
            )
            raise ValueError(msg)

        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_IDENTITY_UPDATED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            updated = identity.model_copy(update=updates)
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_IDENTITY_UPDATED,
            agent_id=key,
            updated_fields=sorted(updates.keys()),
        )
        await self._snapshot(updated, saved_by=f"update_identity:{key}")
        return updated

    async def evolve_identity(
        self,
        agent_id: NotBlankStr,
        evolved_identity: AgentIdentity,
        *,
        evolution_rationale: str,
    ) -> AgentIdentity:
        """Apply an evolved identity after evolution guards have passed.

        Replaces the agent's identity wholesale. Unlike
        ``update_identity`` (which restricts to an allowlist), this
        method accepts any field changes because the evolution pipeline
        has already validated them through guards.

        Immutable identifiers (``id``, ``name``, ``department``) must
        match the existing identity.

        Args:
            agent_id: The agent to evolve.
            evolved_identity: The complete new identity.
            evolution_rationale: Human-readable reason (for audit).

        Returns:
            The updated agent identity.

        Raises:
            AgentNotFoundError: If agent not found.
            ValueError: If immutable fields differ.
        """
        key = str(agent_id)
        async with self._lock:
            current = self._agents.get(key)
            if current is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_IDENTITY_EVOLVED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            if str(evolved_identity.id) != str(current.id):
                msg = (
                    f"evolved_identity.id {evolved_identity.id} "
                    f"does not match current id {current.id}"
                )
                logger.warning(
                    HR_REGISTRY_IDENTITY_EVOLVED,
                    agent_id=key,
                    error=msg,
                )
                raise ValueError(msg)
            if str(evolved_identity.name) != str(current.name):
                msg = "name cannot be changed during evolution"
                logger.warning(
                    HR_REGISTRY_IDENTITY_EVOLVED,
                    agent_id=key,
                    error=msg,
                )
                raise ValueError(msg)
            if str(evolved_identity.department) != str(current.department):
                msg = "department cannot be changed during evolution"
                logger.warning(
                    HR_REGISTRY_IDENTITY_EVOLVED,
                    agent_id=key,
                    error=msg,
                )
                raise ValueError(msg)
            self._agents[key] = evolved_identity

        logger.info(
            HR_REGISTRY_IDENTITY_EVOLVED,
            agent_id=key,
            agent_name=str(evolved_identity.name),
            evolution_rationale=evolution_rationale,
        )
        await self._snapshot(
            evolved_identity,
            saved_by=f"evolution:{evolution_rationale}",
        )
        return evolved_identity

    # Fields the MCP write facade is allowed to mutate via
    # ``apply_identity_update``.  ``id`` / ``name`` / ``department`` are
    # truly immutable identifiers; ``status`` mutates via
    # ``update_status`` so its lifecycle event fires.  Everything else
    # on ``AgentIdentity`` is fair game from the MCP server.
    _BLOCKED_UPDATE_FIELDS: frozenset[str] = frozenset(
        {"id", "name", "department", "status"},
    )

    async def apply_identity_update(
        self,
        agent_id: NotBlankStr,
        updates: dict[str, object],
        *,
        saved_by: str,
    ) -> AgentIdentity:
        """Mutate any allowed field on the registered identity.

        Designed for the MCP write surface, which is privileged and
        must be able to update everything the REST API can. Only the
        truly-immutable identifiers (``id``, ``name``, ``department``)
        and the lifecycle ``status`` slot (which has its own
        ``update_status`` path) are rejected.

        Args:
            agent_id: The agent identifier.
            updates: Mapping of field name to new value.
            saved_by: Actor recorded in the version snapshot.

        Returns:
            Updated agent identity (a new frozen instance).

        Raises:
            AgentNotFoundError: If the agent is not registered.
            ValueError: If ``updates`` contains a blocked field.
        """
        blocked = set(updates.keys()) & self._BLOCKED_UPDATE_FIELDS
        if blocked:
            msg = (
                f"Fields are immutable via apply_identity_update: "
                f"{sorted(blocked)}. Use update_status / evolve_identity "
                f"or accept the immutability for {sorted(blocked)}."
            )
            logger.warning(
                HR_REGISTRY_IDENTITY_UPDATED,
                agent_id=str(agent_id),
                error=msg,
                updated_fields=sorted(updates.keys()),
            )
            raise ValueError(msg)

        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    HR_REGISTRY_IDENTITY_UPDATED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            if not updates:
                # No-op: avoid an unnecessary model_copy + snapshot.
                return identity
            # ``model_copy(update=...)`` bypasses Pydantic validation,
            # so callers (notably the MCP ``synthorg_agents_update``
            # tool) could otherwise smuggle a wrong runtime type for
            # any allowed field (e.g. an int for a ``NotBlankStr``).
            # Re-run validation on the merged dump to enforce the same
            # type / constraint guarantees the construction path
            # already provides.
            from pydantic import ValidationError  # noqa: PLC0415

            merged = identity.model_copy(update=dict(updates)).model_dump()
            try:
                updated = AgentIdentity.model_validate(merged)
            except ValidationError as exc:
                logger.warning(
                    HR_REGISTRY_IDENTITY_UPDATED,
                    agent_id=key,
                    error="invalid update payload",
                    updated_fields=sorted(updates.keys()),
                )
                msg = (
                    f"Update payload for agent {agent_id!r} failed validation: "
                    f"{safe_error_description(exc)}"
                )
                raise ValueError(msg) from exc
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_IDENTITY_UPDATED,
            agent_id=key,
            updated_fields=sorted(updates.keys()),
        )
        await self._snapshot(updated, saved_by=saved_by)
        return updated

    async def update_autonomy(
        self,
        agent_id: NotBlankStr,
        update: AutonomyUpdate,
        *,
        approval_store: ApprovalStoreProtocol | None = None,
        clock: Clock | None = None,
    ) -> AutonomyUpdateResult:
        """Request an autonomy level change for an agent.

        Routes through :class:`AutonomyWorkflow`, which owns the
        multi-step request flow (audit log -> conditional approval
        enqueue -> conditional mutation -> dual-write audit row). The
        method remains on the registry so existing call sites do not
        have to thread the workflow's DI themselves; the workflow's
        cohesive responsibility lives in
        :mod:`synthorg.hr.autonomy_workflow`.

        Args:
            agent_id: The agent whose autonomy is being changed.
            update: The autonomy change request.
            approval_store: Optional approval store; when provided, the
                request is enqueued and the returned ``approval_id``
                identifies it.
            clock: Optional clock seam forwarded to the workflow so
                approval-row timestamps come from the injected clock
                rather than the real wall clock. Defaults to a
                ``SystemClock`` inside the workflow when ``None``.

        Returns:
            ``AutonomyUpdateResult`` describing the outcome.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        from synthorg.hr.autonomy_workflow import (  # noqa: PLC0415
            AutonomyWorkflow,
        )

        workflow = AutonomyWorkflow(
            self,
            approval_store=approval_store,
            clock=clock,
        )
        return await workflow.request(agent_id, update)

    async def snapshot_current_autonomy_level(
        self,
        agent_id: NotBlankStr,
    ) -> AutonomyLevel:
        """Return the agent's current autonomy level under the registry lock.

        Public helper consumed by :class:`AutonomyWorkflow` so the
        workflow can read the prior level without reaching into
        registry internals. Returns ``SUPERVISED`` when the identity
        carries no explicit autonomy level so callers always observe
        a concrete level rather than ``None``.

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent's current ``AutonomyLevel``.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        key = str(agent_id)
        async with self._lock:
            identity = self._agents.get(key)
            if identity is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
                    agent_id=key,
                    error=msg,
                )
                raise AgentNotFoundError(msg)
            return (
                identity.autonomy_level
                if identity.autonomy_level is not None
                else AutonomyLevel.SUPERVISED
            )

    async def apply_autonomy_level(
        self,
        agent_id: NotBlankStr,
        level: AutonomyLevel,
        *,
        saved_by: str,
    ) -> AgentIdentity:
        """Mutate the agent's autonomy level under the registry lock.

        Public helper consumed by :class:`AutonomyWorkflow` so the
        workflow can apply a granted promotion without reaching into
        registry internals. The mutation runs under ``self._lock`` so
        it serialises with the rest of the registry's CRUD writes; a
        versioning snapshot fires after the lock is released so DB I/O
        cannot block concurrent reads (mirrors :meth:`_snapshot`'s
        contract).

        Args:
            agent_id: The agent identifier.
            level: The new autonomy level.
            saved_by: Audit attribution stamped on the version snapshot.

        Returns:
            The updated agent identity.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        key = str(agent_id)
        async with self._lock:
            live = self._agents.get(key)
            if live is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
                    agent_id=key,
                    error=msg,
                    requested_level=level.value,
                    saved_by=saved_by,
                )
                raise AgentNotFoundError(msg)
            prior_level = live.autonomy_level
            applied = live.model_copy(update={"autonomy_level": level})
            self._agents[key] = applied
        await self._snapshot(applied, saved_by=saved_by)
        # Only record a transition when the level actually changed from a
        # concrete prior state. Emitting on a no-op set (prior == new) or a
        # null prior would write a spurious transition into the audit
        # stream that consumers cannot distinguish from a real change.
        if prior_level is not None and prior_level != level:
            logger.info(
                HR_AGENT_STATUS_TRANSITIONED,
                agent_id=key,
                from_status=prior_level.value,
                to_status=level.value,
                saved_by=saved_by,
            )
        return applied

    async def apply_autonomy_update_atomic(
        self,
        agent_id: NotBlankStr,
        level: AutonomyLevel,
        *,
        saved_by: str,
    ) -> tuple[AutonomyLevel, AgentIdentity]:
        """Read prior autonomy level and apply the new level under one lock.

        Atomic counterpart to ``snapshot_current_autonomy_level`` plus
        ``apply_autonomy_level``: the read and the write happen inside
        a single ``self._lock`` acquisition so no concurrent writer
        can land a different mutation between the two steps. The
        returned previous level is the value that actually preceded
        the mutation in this registry, suitable for stamping into
        APPROVED audit rows and state-transition logs without a stale
        snapshot race.

        Falls back to ``SUPERVISED`` when the prior identity carries
        no explicit autonomy level, matching
        ``snapshot_current_autonomy_level`` so callers always observe
        a concrete previous level.

        Args:
            agent_id: The agent identifier.
            level: The new autonomy level to apply.
            saved_by: Audit attribution stamped on the version snapshot.

        Returns:
            Tuple of ``(previous_level, updated_identity)``.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        key = str(agent_id)
        async with self._lock:
            live = self._agents.get(key)
            if live is None:
                msg = f"Agent {agent_id!r} not found in registry"
                logger.warning(
                    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
                    agent_id=key,
                    error=msg,
                    requested_level=level.value,
                    saved_by=saved_by,
                )
                raise AgentNotFoundError(msg)
            previous_level = (
                live.autonomy_level
                if live.autonomy_level is not None
                else AutonomyLevel.SUPERVISED
            )
            applied = live.model_copy(update={"autonomy_level": level})
            self._agents[key] = applied
        await self._snapshot(applied, saved_by=saved_by)
        # Mirror ``apply_autonomy_level``: record a transition only on a real
        # level change (the snapshotted write succeeded above), so the atomic
        # promote / demote path appears in the
        # ``hr.agent.status_transitioned`` stream like the non-atomic one.
        if previous_level != level:
            logger.info(
                HR_AGENT_STATUS_TRANSITIONED,
                agent_id=key,
                from_status=previous_level.value,
                to_status=level.value,
                saved_by=saved_by,
            )
        return previous_level, applied

    async def agent_count(self) -> int:
        """Number of agents currently in the registry.

        Returns:
            Result of type ``int``.
        """
        async with self._lock:
            return len(self._agents)

    async def _snapshot(self, identity: AgentIdentity, *, saved_by: str) -> None:
        """Snapshot identity via versioning service (best-effort, no-op if absent).

        Called **outside** the registry lock in both ``register`` and
        ``update_identity`` -- this is intentional: holding the lock during
        I/O would block all concurrent reads for the duration of the DB write.
        The versioning call is awaited here, but failures are best-effort:
        a ``PersistenceError`` is logged and never re-raised so that registry
        operations always succeed even when the versioning back-end is
        unavailable.
        """
        if self._versioning is None:
            return
        # Local import breaks a circular dependency:
        # persistence.__init__ -> workflow_definition_repo -> engine.workflow
        # -> communication -> hr.registry
        from synthorg.core.persistence_errors import PersistenceError  # noqa: PLC0415

        try:
            await self._versioning.snapshot_if_changed(
                str(identity.id), identity, saved_by
            )
        except PersistenceError as exc:
            logger.warning(
                VERSION_SNAPSHOT_FAILED,
                agent_id=str(identity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
