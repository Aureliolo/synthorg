# mypy: disable-error-code="explicit-any"
"""Tests for AgentRegistryService."""

import pytest
import structlog.testing

from synthorg.core.enums import AgentStatus, SeniorityLevel
from synthorg.hr.errors import AgentAlreadyRegisteredError, AgentNotFoundError
from synthorg.hr.registry import AgentRegistryService
from tests.unit.hr.conftest import make_agent_identity


@pytest.mark.unit
class TestAgentRegistryService:
    """AgentRegistryService registration and lookup."""

    async def test_register_and_get(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        result = await registry.get(str(identity.id))
        assert result is not None
        assert result.name == "alice"

    async def test_register_duplicate_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        with pytest.raises(AgentAlreadyRegisteredError, match="already registered"):
            await registry.register(identity)

    async def test_unregister(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        removed = await registry.unregister(str(identity.id))
        assert removed.name == "alice"
        assert await registry.get(str(identity.id)) is None

    async def test_unregister_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        with pytest.raises(AgentNotFoundError, match="not found"):
            await registry.unregister("nonexistent")

    async def test_get_nonexistent_returns_none(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.get("nonexistent")
        assert result is None

    async def test_get_by_name(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="Bob")
        await registry.register(identity)
        result = await registry.get_by_name("bob")  # case-insensitive
        assert result is not None
        assert result.name == "Bob"

    async def test_get_by_name_not_found(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.get_by_name("nobody")
        assert result is None

    async def test_get_by_names_preserves_order_and_nones(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Batch lookup returns results in input order with None for misses."""
        alice = make_agent_identity(name="Alice")
        bob = make_agent_identity(name="Bob")
        await registry.register(alice)
        await registry.register(bob)

        results = await registry.get_by_names(("Bob", "nobody", "Alice"))
        assert len(results) == 3
        assert results[0] is not None
        assert results[0].name == "Bob"
        assert results[1] is None
        assert results[2] is not None
        assert results[2].name == "Alice"

    async def test_get_by_names_empty_input(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Empty batch returns an empty tuple without touching the lock."""
        results = await registry.get_by_names(())
        assert results == ()

    async def test_get_by_names_duplicates_preserved_in_output(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Duplicate names in input produce duplicate matches in output."""
        alice = make_agent_identity(name="Alice")
        bob = make_agent_identity(name="Bob")
        await registry.register(alice)
        await registry.register(bob)

        results = await registry.get_by_names(("Alice", "Bob", "Alice"))
        assert len(results) == 3
        assert results[0] is not None
        assert results[1] is not None
        assert results[2] is not None
        assert results[0].name == "Alice"
        assert results[1].name == "Bob"
        assert results[2].name == "Alice"

    async def test_get_by_names_acquires_lock_once(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Batch lookup must acquire the registry lock exactly once."""
        import asyncio

        alice = make_agent_identity(name="Alice")
        bob = make_agent_identity(name="Bob")
        carol = make_agent_identity(name="Carol")
        await registry.register(alice)
        await registry.register(bob)
        await registry.register(carol)

        real_lock = registry._lock
        acquire_count = 0

        class CountingLock:
            async def __aenter__(self) -> None:
                nonlocal acquire_count
                # Count only successful acquisitions.  If ``acquire()``
                # propagates (e.g. cancellation), we never took the
                # lock and ``__aexit__`` will not fire, so the counter
                # must stay consistent with released acquisitions.
                await real_lock.acquire()
                acquire_count += 1

            async def __aexit__(
                self,
                *args: object,
            ) -> None:
                real_lock.release()

        registry._lock = CountingLock()  # type: ignore[assignment]
        try:
            results = await registry.get_by_names(
                ("Alice", "Bob", "Carol", "Alice", "missing"),
            )
        finally:
            registry._lock = real_lock

        assert acquire_count == 1
        assert len(results) == 5
        assert results[4] is None  # missing
        # No deadlock; the real lock is still usable.
        assert isinstance(real_lock, asyncio.Lock)
        assert not real_lock.locked()

    async def test_get_by_names_rejects_oversized_batch(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Exceeding ``MAX_BATCH_NAMES_LOOKUP`` must raise ``ValueError``."""
        from synthorg.hr.registry import MAX_BATCH_NAMES_LOOKUP

        names = tuple(f"agent-{i}" for i in range(MAX_BATCH_NAMES_LOOKUP + 1))
        with pytest.raises(ValueError, match="exceeds"):
            await registry.get_by_names(names)

    async def test_list_active_filters_status(
        self,
        registry: AgentRegistryService,
    ) -> None:
        active = make_agent_identity(name="active-agent", status=AgentStatus.ACTIVE)
        onboarding = make_agent_identity(
            name="onboarding-agent",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(active)
        await registry.register(onboarding)
        result = await registry.list_active()
        assert len(result) == 1
        assert result[0].name == "active-agent"

    async def test_list_active_empty(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.list_active()
        assert result == ()

    async def test_list_by_department(
        self,
        registry: AgentRegistryService,
    ) -> None:
        eng = make_agent_identity(name="eng-agent", department="engineering")
        design = make_agent_identity(name="design-agent", department="design")
        await registry.register(eng)
        await registry.register(design)
        result = await registry.list_by_department("engineering")
        assert len(result) == 1
        assert result[0].name == "eng-agent"

    async def test_list_by_department_case_insensitive(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="agent", department="Engineering")
        await registry.register(identity)
        result = await registry.list_by_department("ENGINEERING")
        assert len(result) == 1

    async def test_update_status(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice", status=AgentStatus.ACTIVE)
        await registry.register(identity)
        updated = await registry.update_status(
            str(identity.id),
            AgentStatus.ON_LEAVE,
        )
        assert updated.status == AgentStatus.ON_LEAVE
        # Verify stored value is also updated.
        fetched = await registry.get(str(identity.id))
        assert fetched is not None
        assert fetched.status == AgentStatus.ON_LEAVE

    async def test_update_status_emits_transition_event(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """update_status emits HR_AGENT_STATUS_TRANSITIONED with
        from_status / to_status / agent_id AFTER the persistence
        write so the audit stream records every persisted hop."""
        identity = make_agent_identity(name="alice", status=AgentStatus.ACTIVE)
        await registry.register(identity)
        with structlog.testing.capture_logs() as events:
            await registry.update_status(
                str(identity.id),
                AgentStatus.ON_LEAVE,
            )
        transition_events = [
            e for e in events if e.get("event") == "hr.agent.status_transitioned"
        ]
        assert len(transition_events) == 1
        entry = transition_events[0]
        assert entry["from_status"] == "active"
        assert entry["to_status"] == "on_leave"
        assert entry["agent_id"] == str(identity.id)

    async def test_update_status_noop_skips_transition_event(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """No-op transitions (status unchanged) skip the transition
        event so the audit stream records actual state changes only."""
        identity = make_agent_identity(name="alice", status=AgentStatus.ACTIVE)
        await registry.register(identity)
        with structlog.testing.capture_logs() as events:
            await registry.update_status(
                str(identity.id),
                AgentStatus.ACTIVE,
            )
        transition_events = [
            e for e in events if e.get("event") == "hr.agent.status_transitioned"
        ]
        assert transition_events == []

    async def test_update_status_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        with pytest.raises(AgentNotFoundError, match="not found"):
            await registry.update_status("nonexistent", AgentStatus.TERMINATED)

    async def test_agent_count_empty(
        self,
        registry: AgentRegistryService,
    ) -> None:
        assert await registry.agent_count() == 0

    async def test_agent_count_tracks_registrations(
        self,
        registry: AgentRegistryService,
    ) -> None:
        a = make_agent_identity(name="alice")
        b = make_agent_identity(name="bob")
        await registry.register(a)
        assert await registry.agent_count() == 1
        await registry.register(b)
        assert await registry.agent_count() == 2
        await registry.unregister(str(a.id))
        assert await registry.agent_count() == 1

    async def test_update_identity(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        updated = await registry.update_identity(
            str(identity.id),
            level=SeniorityLevel.SENIOR,
        )
        assert updated.level == SeniorityLevel.SENIOR
        # Original identity is not mutated
        assert identity.level == SeniorityLevel.MID
        # Stored value is updated
        fetched = await registry.get(str(identity.id))
        assert fetched is not None
        assert fetched.level == SeniorityLevel.SENIOR

    async def test_update_identity_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        with pytest.raises(AgentNotFoundError, match="not found"):
            await registry.update_identity(
                "nonexistent",
                level=SeniorityLevel.SENIOR,
            )

    async def test_update_identity_disallowed_field_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Fields not in the allowlist are rejected."""
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        with pytest.raises(ValueError, match="not allowed"):
            await registry.update_identity(
                str(identity.id),
                status=AgentStatus.ON_LEAVE,
            )


@pytest.mark.unit
class TestBindVersioning:
    """Post-construction versioning attachment used by the app factory."""

    async def test_bind_versioning_activates_snapshot_creation(self) -> None:
        """``bind_versioning`` wires a versioning service after construction.

        Simulates the app-factory flow: ``create_app`` builds the registry
        synchronously, then ``on_startup`` attaches versioning once
        persistence is connected.
        """
        from synthorg.core.agent import AgentIdentity
        from synthorg.versioning import VersioningService
        from tests.unit.api.fakes_backend import FakeVersionRepository

        registry = AgentRegistryService()
        repo: FakeVersionRepository[AgentIdentity] = FakeVersionRepository()
        versioning: VersioningService[AgentIdentity] = VersioningService(repo)
        registry.bind_versioning(versioning)
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        versions = await repo.list_versions(str(identity.id), limit=10, offset=0)
        assert len(versions) == 1
        assert versions[0].version == 1
