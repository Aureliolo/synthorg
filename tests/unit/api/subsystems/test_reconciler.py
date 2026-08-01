"""Tests for the level-triggered subsystem reconciler.

The properties under test are the ones the pattern rests on: a pass is
idempotent, a dependency that arrives late still brings its dependents up,
and a subsystem that cannot activate is recorded rather than allowed to
abort the pass or to be forgotten.
"""

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
from synthorg.api.subsystems.reconciler import SubsystemReconciler
from synthorg.api.subsystems.spec import (
    Activate,
    Capability,
    CapabilityId,
    Deactivate,
    SubsystemPhase,
    SubsystemSpec,
)
from synthorg.config.schema import RootConfig


class _World:
    """Mutable stand-in for whatever the real capability probes read."""

    def __init__(self, *present: CapabilityId) -> None:
        self.present: set[CapabilityId] = set(present)
        self.activations: list[str] = []
        self.deactivations: list[str] = []


def _capability(world: _World, cap_id: CapabilityId) -> Capability:
    """Build a probe reading availability out of the test world."""
    return Capability(id=cap_id, present=lambda _state: cap_id in world.present)


def _installs(world: _World, name: str, cap_id: CapabilityId) -> Activate:
    """Build an activation that installs its own capability, as real wiring does."""

    async def _activate(_state: AppState) -> None:
        world.activations.append(name)
        world.present.add(cap_id)

    return _activate


def _removes(world: _World, name: str, cap_id: CapabilityId) -> Deactivate:
    """Build a teardown that withdraws its own capability."""

    async def _deactivate(_state: AppState) -> None:
        world.deactivations.append(name)
        world.present.discard(cap_id)

    return _deactivate


def _app_state() -> AppState:
    return AppState(config=RootConfig(company_name="test"))


def _all_capabilities(world: _World) -> tuple[Capability, ...]:
    return tuple(_capability(world, cap_id) for cap_id in CapabilityId)


@pytest.mark.unit
class TestOrdering:
    """Activation order comes from the declarations, not a written sequence."""

    def test_provider_is_ordered_before_its_consumer(self) -> None:
        world = _World()
        consumer = SubsystemSpec(
            name="consumer",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "consumer", CapabilityId.MEMORY_BACKEND),
        )
        provider = SubsystemSpec(
            name="provider",
            provides=CapabilityId.PERSISTENCE,
            activate=_installs(world, "provider", CapabilityId.PERSISTENCE),
        )
        # Declared consumer-first on purpose: order must come from the
        # dependency edges, not from how the table happens to be written.
        reconciler = SubsystemReconciler((consumer, provider), _all_capabilities(world))
        ordered = [status.name for status in reconciler.statuses(_app_state())]
        assert ordered == ["provider", "consumer"]

    def test_a_cycle_is_refused_at_construction(self) -> None:
        world = _World()
        first = SubsystemSpec(
            name="first",
            provides=CapabilityId.PERSISTENCE,
            requires=(CapabilityId.MEMORY_BACKEND,),
            activate=_installs(world, "first", CapabilityId.PERSISTENCE),
        )
        second = SubsystemSpec(
            name="second",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "second", CapabilityId.MEMORY_BACKEND),
        )
        with pytest.raises(SubsystemGraphInvalidError, match="cycle"):
            SubsystemReconciler((first, second), _all_capabilities(world))

    def test_rebuild_without_a_teardown_is_refused(self) -> None:
        # A rebuild is deactivate-then-activate. With no teardown the
        # subsystem still reads active, the pass leaves it alone, and the
        # declaration promises a replacement that never happens. Refused at
        # the declaration itself, so it fails where it was written rather
        # than in whichever build first orders it.
        world = _World()
        with pytest.raises(ValueError, match="deactivate"):
            SubsystemSpec(
                name="memory",
                provides=CapabilityId.MEMORY_BACKEND,
                activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
                settings=("memory.backend",),
                rebuild_on_change=True,
            )

    def test_a_consumer_of_a_tearable_capability_needs_its_own_teardown(self) -> None:
        # The owner can take MEMORY_BACKEND away while the process runs, so a
        # consumer that captured it and cannot be taken down would keep
        # serving from a disconnected collaborator and still read active.
        world = _World()
        owner = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        consumer = SubsystemSpec(
            name="docs",
            provides=CapabilityId.DOCS_ENGINE,
            requires=(CapabilityId.MEMORY_BACKEND,),
            activate=_installs(world, "docs", CapabilityId.DOCS_ENGINE),
        )
        with pytest.raises(SubsystemGraphInvalidError, match="no deactivate"):
            SubsystemReconciler((owner, consumer), _all_capabilities(world))

    def test_a_consumer_of_a_tearable_capability_needs_a_rebuild(self) -> None:
        # A teardown alone is not enough: a replacement that arrives without
        # a rebuild leaves the consumer holding the instance that was
        # replaced.
        world = _World()
        owner = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        consumer = SubsystemSpec(
            name="docs",
            provides=CapabilityId.DOCS_ENGINE,
            requires=(CapabilityId.MEMORY_BACKEND,),
            activate=_installs(world, "docs", CapabilityId.DOCS_ENGINE),
            deactivate=_removes(world, "docs", CapabilityId.DOCS_ENGINE),
        )
        with pytest.raises(SubsystemGraphInvalidError, match="no rebuild_on_change"):
            SubsystemReconciler((owner, consumer), _all_capabilities(world))

    def test_two_owners_of_one_capability_are_refused(self) -> None:
        world = _World()
        specs = tuple(
            SubsystemSpec(
                name=name,
                provides=CapabilityId.MEMORY_BACKEND,
                activate=_installs(world, name, CapabilityId.MEMORY_BACKEND),
            )
            for name in ("one", "two")
        )
        with pytest.raises(SubsystemGraphInvalidError, match="one owner"):
            SubsystemReconciler(specs, _all_capabilities(world))


@pytest.mark.unit
class TestReconcile:
    """The level-triggered pass itself."""

    async def test_a_second_pass_does_nothing(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        first = await reconciler.reconcile(state, trigger="boot")
        second = await reconciler.reconcile(state, trigger="resync")

        assert first.activated == ("memory",)
        # Idempotence is the property the whole pattern rests on: without it
        # every trigger would rebuild the world.
        assert second.activated == ()
        assert world.activations == ["memory"]

    async def test_a_dependency_arriving_late_still_brings_it_up(self) -> None:
        world = _World()
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        boot = await reconciler.reconcile(state, trigger="boot")
        assert boot.activated == ()
        assert boot.statuses[0].phase is SubsystemPhase.WAITING

        # The dependency shows up after boot, which under the old boot-time
        # wiring was a permanent verdict requiring a restart.
        world.present.add(CapabilityId.PERSISTENCE)
        later = await reconciler.reconcile(state, trigger="resync")

        assert later.activated == ("memory",)
        assert later.statuses[0].phase is SubsystemPhase.ACTIVE

    async def test_waiting_names_every_unmet_requirement(self) -> None:
        world = _World()
        spec = SubsystemSpec(
            name="docs",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            requires=(CapabilityId.PERSISTENCE, CapabilityId.MEMORY_BACKEND),
            activate=_installs(world, "docs", CapabilityId.KNOWLEDGE_ENGINE),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")

        # Naming only the first would send an operator round the loop once
        # per missing dependency.
        assert report.statuses[0].waiting_on == (
            CapabilityId.PERSISTENCE,
            CapabilityId.MEMORY_BACKEND,
        )

    async def test_losing_a_dependency_takes_the_subsystem_down(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        await reconciler.reconcile(state, trigger="boot")

        world.present.discard(CapabilityId.PERSISTENCE)
        report = await reconciler.reconcile(state, trigger="resync")

        assert report.deactivated == ("memory",)
        assert world.deactivations == ["memory"]

    async def test_a_failing_activation_is_recorded_and_retried(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []

        async def _explode(_state: AppState) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                msg = "backend refused the connection"
                raise RuntimeError(msg)
            world.present.add(CapabilityId.MEMORY_BACKEND)

        healthy = SubsystemSpec(
            name="healthy",
            provides=CapabilityId.ORG_MEMORY_BACKEND,
            activate=_installs(world, "healthy", CapabilityId.ORG_MEMORY_BACKEND),
        )
        broken = SubsystemSpec(
            name="broken",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_explode,
        )
        reconciler = SubsystemReconciler((broken, healthy), _all_capabilities(world))
        state = _app_state()

        first = await reconciler.reconcile(state, trigger="boot")
        assert first.failed == ("broken",)
        # One subsystem that cannot come up must not stop the others.
        assert "healthy" in first.activated

        second = await reconciler.reconcile(state, trigger="resync")
        assert second.activated == ("broken",)
        assert second.failed == ()

    async def test_activation_that_declines_reports_blocked_not_waiting(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)

        async def _decline(_state: AppState) -> None:
            """Return without installing, as a wiring hook's own gate does."""

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")

        assert report.activated == ()
        # Every declared dependency is present, so "waiting" would name
        # nothing and send an operator looking for a missing dependency that
        # does not exist. The subsystem declined on a condition of its own
        # (memory with no embedding model chosen) and logged why.
        assert report.statuses[0].phase is SubsystemPhase.BLOCKED
        assert report.statuses[0].waiting_on == ()

    async def test_a_blocked_subsystem_recovers_without_intervention(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        allow = {"now": False}

        async def _decline_until_allowed(_state: AppState) -> None:
            if allow["now"]:
                world.present.add(CapabilityId.MEMORY_BACKEND)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline_until_allowed,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        first = await reconciler.reconcile(state, trigger="boot")
        assert first.statuses[0].phase is SubsystemPhase.BLOCKED

        # The operator configures the embedder. Nothing announces it, which
        # is exactly why the sweep keeps asking.
        allow["now"] = True
        later = await reconciler.reconcile(state, trigger="resync")

        assert later.activated == ("memory",)
        assert later.statuses[0].phase is SubsystemPhase.ACTIVE
