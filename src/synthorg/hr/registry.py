"""Agent registry service.

Hot-pluggable agent registry for tracking active agents,
their identities, and lifecycle status transitions (D8.3).
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.enums import (
    AgentStatus,
    ApprovalRiskLevel,
    ApprovalStatus,
    AutonomyLevel,
)
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_REGISTRY_AGENT_REGISTERED,
    HR_REGISTRY_AGENT_REMOVED,
    HR_REGISTRY_CLEARED,
    HR_REGISTRY_IDENTITY_EVOLVED,
    HR_REGISTRY_IDENTITY_UPDATED,
    HR_REGISTRY_STATUS_UPDATED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.observability.events.versioning import VERSION_SNAPSHOT_FAILED
from synthorg.security.autonomy.models import AutonomyUpdate, AutonomyUpdateResult

# Upper bound on a single ``get_by_names`` batch.  Caller inputs can
# originate from user-supplied request bodies (e.g. the coordination
# endpoint's ``agent_names``), so the batch must not block the
# registry's single ``asyncio.Lock`` for an unbounded period.  A
# well-formed organisation has far fewer active agents than this
# ceiling; anything larger is assumed to be misuse.
MAX_BATCH_NAMES_LOOKUP: Final[int] = 1024

if TYPE_CHECKING:
    from typing import Any

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr
    from synthorg.versioning.service import VersioningService

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
        the contending writer claimed (#1599).
        """
        async with self._lock:
            cleared_count = len(self._agents)
            self._agents.clear()
        logger.info(HR_REGISTRY_CLEARED, cleared_count=cleared_count)

    def reset_for_test_sync(self) -> None:
        """Synchronous reset for sync pytest fixtures only.

        Bypasses ``self._lock`` -- callers must guarantee no async
        operations are in flight. Production code MUST use the async
        ``clear`` instead. Provided so existing sync fixtures can keep
        their iteration shape after #1599 made ``clear`` async.
        """
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

    async def get_by_name(self, name: NotBlankStr) -> AgentIdentity | None:
        """Retrieve an agent identity by name.

        Args:
            name: The agent name to search for.

        Returns:
            The first matching agent, or None.
        """
        async with self._lock:
            name_lower = str(name).lower()
            for identity in self._agents.values():
                if str(identity.name).lower() == name_lower:
                    return identity
            return None

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
            by_lower_name: dict[str, AgentIdentity] = {}
            for identity in self._agents.values():
                key = str(identity.name).lower()
                # First registration wins on name collision, matching
                # ``get_by_name`` semantics.
                by_lower_name.setdefault(key, identity)
            return tuple(by_lower_name.get(str(name).lower()) for name in names)

    async def list_active(self) -> tuple[AgentIdentity, ...]:
        """List all agents with ACTIVE status.

        Returns:
            Tuple of active agent identities.
        """
        async with self._lock:
            return tuple(
                a for a in self._agents.values() if a.status == AgentStatus.ACTIVE
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
            dept_lower = str(department).lower()
            return tuple(
                a
                for a in self._agents.values()
                if str(a.department).lower() == dept_lower
            )

    async def update_status(
        self,
        agent_id: NotBlankStr,
        status: AgentStatus,
    ) -> AgentIdentity:
        """Update an agent's lifecycle status.

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
            updated = identity.model_copy(update={"status": status})
            self._agents[key] = updated

        logger.info(
            HR_REGISTRY_STATUS_UPDATED,
            agent_id=key,
            status=status.value,
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
        **updates: Any,
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
        updates: dict[str, Any],
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

            from synthorg.core.agent import AgentIdentity  # noqa: PLC0415

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
    ) -> AutonomyUpdateResult:
        """Request an autonomy level change for an agent.

        Mirrors the REST endpoint: the change is *requested*, never
        applied directly. ``SECURITY_AUTONOMY_PROMOTION_REQUESTED`` is logged
        for the audit trail; an approval item is enqueued when an
        ``approval_store`` is wired so the queue can drive the human
        review; ``SECURITY_AUTONOMY_PROMOTION_DENIED`` is logged because the
        request did not produce an immediate runtime change.

        Args:
            agent_id: The agent whose autonomy is being changed.
            update: The autonomy change request.
            approval_store: Optional approval store; when provided, the
                request is enqueued and the returned ``approval_id``
                identifies it.

        Returns:
            ``AutonomyUpdateResult`` describing the outcome.

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
            current_level: AutonomyLevel = (
                identity.autonomy_level
                if identity.autonomy_level is not None
                else AutonomyLevel.SUPERVISED
            )

        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=key,
            requested_level=update.requested_level.value,
            current_level=current_level.value,
            reason=update.reason,
            requested_by=update.requested_by,
        )

        approval_id: str | None = None
        approval_enqueued = False
        if approval_store is not None:
            # Local import breaks the import cycle:
            # ``synthorg.core.approval`` -> ``synthorg.ontology.decorator`` ->
            # ... -> ``synthorg.communication.meeting.participant`` ->
            # ``synthorg.hr.registry``. The class is only needed inside this
            # branch, so deferring the import to call time keeps module
            # bootstrap acyclic without weakening the call-site contract.
            from synthorg.core.approval import (  # noqa: PLC0415
                ApprovalItem as _ApprovalItem,
            )

            # 16 hex chars (64 bits) keeps collision probability negligible
            # for approval-queue volumes while still fitting compactly into
            # log lines and audit trails.
            approval_id = f"approval-{uuid.uuid4().hex[:16]}"
            requested_by = update.requested_by or "system"
            item = _ApprovalItem(
                id=approval_id,
                action_type="autonomy:promote",
                title=(
                    f"Autonomy change for {key}: "
                    f"{current_level.value} -> {update.requested_level.value}"
                ),
                description=update.reason,
                requested_by=requested_by,
                risk_level=ApprovalRiskLevel.HIGH,
                status=ApprovalStatus.PENDING,
                created_at=datetime.now(UTC),
                metadata={
                    "agent_id": key,
                    "current_level": current_level.value,
                    "requested_level": update.requested_level.value,
                },
            )
            await approval_store.add(item)
            approval_enqueued = True

        # Mirror REST: every change pends; nothing mutates the agent's
        # identity here.  The approval queue drives any subsequent
        # apply, which is out of scope for META-MCP-3.
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=key,
            requested_level=update.requested_level.value,
            reason="Autonomy level changes require human approval",
        )

        return AutonomyUpdateResult(
            agent_id=key,
            current_level=current_level,
            requested_level=update.requested_level,
            promotion_pending=True,
            approval_enqueued=approval_enqueued,
            approval_id=approval_id,
        )

    async def agent_count(self) -> int:
        """Number of agents currently in the registry."""
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
        from synthorg.persistence.errors import PersistenceError  # noqa: PLC0415

        try:
            await self._versioning.snapshot_if_changed(
                str(identity.id), identity, saved_by
            )
        except PersistenceError as exc:
            logger.warning(
                VERSION_SNAPSHOT_FAILED,
                agent_id=str(identity.id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
