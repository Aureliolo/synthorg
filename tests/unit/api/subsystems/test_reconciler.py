"""Tests for the level-triggered subsystem reconciler.

The properties under test are the ones the pattern rests on: a pass is
idempotent, a dependency that arrives late still brings its dependents up,
and a subsystem that cannot activate is recorded rather than allowed to
abort the pass or to be forgotten.
"""

import asyncio
import threading
from typing import Final
from unittest.mock import patch

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import (
    SubsystemDeclinedError,
    SubsystemGraphInvalidError,
)
from synthorg.api.subsystems.reconciler import SubsystemReconciler
from synthorg.api.subsystems.runtime import reconcile_subsystems
from synthorg.api.subsystems.spec import (
    Activate,
    Capability,
    CapabilityId,
    Deactivate,
    SubsystemPhase,
    SubsystemSpec,
)
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import mock_of

#: Loops contending for one pass. More than two, so a gate that serialises a
#: pair but not a crowd is not mistaken for one that serialises.
_CONTENDING_LOOPS: Final[int] = 4


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

    @pytest.mark.parametrize(
        ("gate", "reason"),
        [
            ("knowledge.enabld", "not a registered"),
            ("enabled", "not a registered"),
            ("memory.backend", "a gate is on or off"),
            ("chief_of_staff.explain_chat_enabled", "absent from the boot config"),
            ("api.setup_complete", "absent from the boot config"),
        ],
        ids=[
            "misspelled-key",
            "no-namespace",
            "not-a-boolean",
            "no-boot-config-section",
            "section-without-the-key",
        ],
    )
    def test_a_gate_that_can_never_read_as_off_is_refused(
        self, gate: str, reason: str
    ) -> None:
        # An unresolvable gate reads as enabled, which is the right default
        # for a subsystem that declares none and the wrong one for a typo:
        # the operator switches it off, the write lands, and nothing changes.
        world = _World()
        spec = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            activate=_installs(world, "knowledge", CapabilityId.KNOWLEDGE_ENGINE),
            enabled_by=gate,
        )
        with pytest.raises(SubsystemGraphInvalidError, match=reason):
            SubsystemReconciler((spec,), _all_capabilities(world))

    def test_an_unowned_requirement_is_refused_with_no_probes_either(self) -> None:
        # With no probes at all, every requirement no subsystem owns is
        # unprobed, so skipping the check because the probe set is empty is
        # exactly the case that fails open: the consumer activates as though
        # the dependency were there.
        world = _World()
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        with pytest.raises(SubsystemGraphInvalidError, match="no capability probes"):
            SubsystemReconciler((spec,), ())

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
        with pytest.raises(SubsystemGraphInvalidError, match="deactivate"):
            SubsystemSpec(
                name="memory",
                provides=CapabilityId.MEMORY_BACKEND,
                activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
                settings=("memory.backend",),
                rebuild_on_change=True,
            )

    def test_an_unregistered_declared_setting_is_refused(self) -> None:
        # settings_fingerprint snapshots an unreadable key as None and the
        # drift comparison skips those, so a misspelled one is never compared
        # on any pass: the rebuild it was declared for could never fire, and
        # nothing would ever say so.
        world = _World()
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            settings=("memory.no_such_key",),
            rebuild_on_change=True,
        )
        with pytest.raises(SubsystemGraphInvalidError, match="not a registered"):
            SubsystemReconciler((spec,), _all_capabilities(world))

    def test_a_setting_without_a_namespace_is_refused(self) -> None:
        world = _World()
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            settings=("backend",),
            rebuild_on_change=True,
        )
        with pytest.raises(SubsystemGraphInvalidError, match="not a registered"):
            SubsystemReconciler((spec,), _all_capabilities(world))

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

    def test_a_duplicate_name_is_refused(self) -> None:
        world = _World()
        specs = (
            SubsystemSpec(
                name="memory",
                provides=CapabilityId.MEMORY_BACKEND,
                activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            ),
            SubsystemSpec(
                name="memory",
                provides=CapabilityId.ORG_MEMORY_BACKEND,
                activate=_installs(world, "memory", CapabilityId.ORG_MEMORY_BACKEND),
            ),
        )
        # The name keys the status surface and every bookkeeping dict, so two
        # declarations sharing one would silently share a decline record.
        with pytest.raises(SubsystemGraphInvalidError, match="declared twice"):
            SubsystemReconciler(specs, _all_capabilities(world))

    def test_a_requirement_nothing_probes_is_refused(self) -> None:
        world = _World()
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.TOOL_CALL_FEEDBACK,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        # Unprobed and unowned, so it would read as an ambient precondition
        # and be satisfied on every pass: the consumer activates as though
        # the dependency were there.
        probes = (_capability(world, CapabilityId.MEMORY_BACKEND),)
        with pytest.raises(SubsystemGraphInvalidError, match="no capability probes"):
            SubsystemReconciler((spec,), probes)


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

    async def test_losing_a_dependency_with_no_teardown_reports_degraded(
        self,
    ) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        await reconciler.reconcile(state, trigger="boot")

        world.present.discard(CapabilityId.PERSISTENCE)
        report = await reconciler.reconcile(state, trigger="resync")

        # Nothing to tear down with, so it stays up holding a collaborator
        # that is gone. Reporting that as active would be the exact drift
        # reading liveness from ``provides`` exists to prevent.
        assert report.deactivated == ()
        assert report.statuses[0].phase is SubsystemPhase.DEGRADED
        assert report.statuses[0].waiting_on == (CapabilityId.PERSISTENCE,)

    async def test_disabling_a_running_subsystem_takes_it_down(self) -> None:
        world = _World(CapabilityId.PERSISTENCE, CapabilityId.KNOWLEDGE_ENGINE)
        spec = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "knowledge", CapabilityId.KNOWLEDGE_ENGINE),
            deactivate=_removes(world, "knowledge", CapabilityId.KNOWLEDGE_ENGINE),
            enabled_by="knowledge.enabled",
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        config = RootConfig(company_name="test")
        state = AppState(
            config=config.model_copy(
                update={
                    "knowledge": config.knowledge.model_copy(update={"enabled": False})
                }
            )
        )

        # Already up when the operator turns it off, so the pass must take it
        # down rather than only decline to bring it up next time.
        report = await reconciler.reconcile(state, trigger="settings_write")

        assert report.deactivated == ("knowledge",)
        assert world.deactivations == ["knowledge"]
        assert report.statuses[0].phase is SubsystemPhase.DISABLED

    async def test_a_replaced_dependency_rebuilds_its_consumer(self) -> None:
        world = _World()
        provider = SubsystemSpec(
            name="provider",
            provides=CapabilityId.PERSISTENCE,
            activate=_installs(world, "provider", CapabilityId.PERSISTENCE),
            deactivate=_removes(world, "provider", CapabilityId.PERSISTENCE),
        )
        consumer = SubsystemSpec(
            name="consumer",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "consumer", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "consumer", CapabilityId.MEMORY_BACKEND),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((provider, consumer), _all_capabilities(world))
        state = _app_state()
        await reconciler.reconcile(state, trigger="boot")
        assert world.activations == ["provider", "consumer"]

        # The provider is replaced within a single pass: ordered first, it
        # reactivates and reinstalls the capability before the consumer is
        # evaluated. Availability alone cannot see that, because it reads
        # present both before and after, while the consumer still holds the
        # instance being replaced.
        world.present.discard(CapabilityId.PERSISTENCE)
        report = await reconciler.reconcile(state, trigger="resync")

        assert world.activations == ["provider", "consumer", "provider", "consumer"]
        assert "consumer" in report.activated
        assert report.statuses[1].phase is SubsystemPhase.ACTIVE

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

        second = await reconciler.reconcile(
            state, trigger="resync", retry_declined=True
        )
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

        # The operator configures the embedder. Nothing announces it, and the
        # subsystem declares nothing that moved, which is exactly why the
        # sweep asks again without needing a reason.
        allow["now"] = True
        later = await reconciler.reconcile(state, trigger="resync", retry_declined=True)

        assert later.activated == ("memory",)
        assert later.statuses[0].phase is SubsystemPhase.ACTIVE


@pytest.mark.unit
class TestPassSerialisation:
    """One pass at a time, whichever loop asks.

    The reconciler is cached on an application state that outlives a single
    event loop, and an ``asyncio.Lock`` only serialises callers sharing the
    loop it bound to. Rebuilding the lock per loop keeps it usable but
    serialises nothing across them, so the gate has to be loop-independent.
    """

    def _reconciler_that_takes_its_time(
        self,
        world: _World,
        seen: list[int],
        overlaps: list[int],
        *,
        entered: threading.Event,
        release: threading.Event,
    ) -> SubsystemReconciler:
        """Build a reconciler whose one activation holds the pass open.

        Args:
            world: Capability source the probes read.
            seen: Appended to once per activation attempt.
            overlaps: Appended to whenever two activations run at once.
            entered: Set once an activation is running, so a caller can start
                its own pass against the open window instead of guessing.
            release: Held inside the activation until set, which makes the
                window last exactly as long as the caller needs. Gated rather
                than slept through, so what the test proves does not depend on
                a sleep outlasting the scheduler.

        Returns:
            A reconciler over one slow subsystem.
        """
        guard = threading.Lock()
        inside = 0

        async def _slow_decline(_state: AppState) -> None:
            nonlocal inside
            with guard:
                inside += 1
                seen.append(1)
                if inside > 1:
                    overlaps.append(inside)
            entered.set()
            # Asserted, not discarded: if a change stops the follower
            # deferring, it blocks here until the timeout and the test
            # would still see two attempts, passing after a stall.
            released = await asyncio.to_thread(release.wait, 10)
            assert released, "the activation window was never released"
            with guard:
                inside -= 1

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_slow_decline,
        )
        return SubsystemReconciler((spec,), _all_capabilities(world))

    def test_two_event_loops_cannot_hold_a_pass_at_once(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        seen: list[int] = []
        overlaps: list[int] = []
        entered = threading.Event()
        release = threading.Event()
        reconciler = self._reconciler_that_takes_its_time(
            world, seen, overlaps, entered=entered, release=release
        )
        state = _app_state()
        # Every thread announces itself before asking for a pass, so the
        # window below is held open until all four have asked rather than for
        # a fixed sleep the scheduler is free to overrun.
        asked = threading.Barrier(_CONTENDING_LOOPS + 1)

        def _pass_on_its_own_loop() -> None:
            asked.wait(timeout=10)
            asyncio.run(
                reconciler.reconcile(state, trigger="thread", retry_declined=True)
            )

        threads = [
            threading.Thread(target=_pass_on_its_own_loop, daemon=True)
            for _ in range(_CONTENDING_LOOPS)
        ]
        for thread in threads:
            thread.start()
        asked.wait(timeout=10)
        assert entered.wait(timeout=10), "no loop ever reached the activation"
        release.set()
        for thread in threads:
            thread.join(timeout=10)

        assert [thread.is_alive() for thread in threads] == [False] * _CONTENDING_LOOPS
        assert overlaps == []
        assert seen

    def test_a_trigger_that_arrives_mid_pass_is_not_dropped(self) -> None:
        # A deferred caller does not wait, so the pass in flight has to carry
        # its trigger: without the hand-off the sweep that arrived while a
        # pass was running would simply never be reconciled. Its
        # retry_declined has to survive too, or the deferred sweep becomes an
        # event trigger and skips the declines it exists to re-attempt.
        world = _World(CapabilityId.PERSISTENCE)
        seen: list[int] = []
        entered = threading.Event()
        release = threading.Event()
        reconciler = self._reconciler_that_takes_its_time(
            world, seen, [], entered=entered, release=release
        )
        state = _app_state()
        deferred = threading.Event()

        def _hold_a_pass() -> None:
            asyncio.run(reconciler.reconcile(state, trigger="holder"))

        holder = threading.Thread(target=_hold_a_pass, daemon=True)
        holder.start()
        # Which caller gets deferred decides whose retry_declined the repeat
        # carries, so the window has to be open before the second one asks.
        assert entered.wait(timeout=10), "the holder never reached its activation"

        def _defer() -> None:
            asyncio.run(
                reconciler.reconcile(state, trigger="resync", retry_declined=True)
            )
            deferred.set()
            release.set()

        follower = threading.Thread(target=_defer, daemon=True)
        follower.start()
        follower.join(timeout=10)
        holder.join(timeout=10)

        assert [follower.is_alive(), holder.is_alive()] == [False, False]
        assert deferred.is_set()
        # Two attempts: the holder's, and the one the deferred sweep earned by
        # having its retry_declined carried onto the repeat.
        assert len(seen) == 2


@pytest.mark.unit
class TestTeardownOrder:
    """Teardown runs in reverse dependency order, the mirror of activation.

    A provider taken down first leaves its consumers live over an instance
    that has gone away, and a request served in that window reads through a
    disconnected collaborator.
    """

    def _pair(
        self, world: _World, events: list[str]
    ) -> tuple[SubsystemSpec, SubsystemSpec]:
        """Declare a provider and a consumer that captured its instance."""

        def _phase(name: str, cap_id: CapabilityId, *, up: bool) -> Activate:
            async def _run(_state: AppState) -> None:
                events.append(f"{'up' if up else 'down'}:{name}")
                if up:
                    world.present.add(cap_id)
                else:
                    world.present.discard(cap_id)

            return _run

        provider = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_phase("memory", CapabilityId.MEMORY_BACKEND, up=True),
            deactivate=_phase("memory", CapabilityId.MEMORY_BACKEND, up=False),
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        consumer = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            requires=(CapabilityId.MEMORY_BACKEND,),
            activate=_phase("knowledge", CapabilityId.KNOWLEDGE_ENGINE, up=True),
            deactivate=_phase("knowledge", CapabilityId.KNOWLEDGE_ENGINE, up=False),
            rebuild_on_change=True,
        )
        return provider, consumer

    def _state_reading(self, values: dict[str, str]) -> AppState:
        """Build application state whose resolver serves *values*."""

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )
        return state

    async def test_a_consumer_goes_down_before_the_provider_it_captured(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        events: list[str] = []
        values = {"memory.backend": "inmemory"}
        reconciler = SubsystemReconciler(
            self._pair(world, events), _all_capabilities(world)
        )
        state = self._state_reading(values)

        await reconciler.reconcile(state, trigger="boot")
        assert events == ["up:memory", "up:knowledge"]

        events.clear()
        values["memory.backend"] = "sqlvector"
        report = await reconciler.reconcile(state, trigger="settings_write")

        assert events == ["down:knowledge", "down:memory", "up:memory", "up:knowledge"]
        # Both came back, so neither is reported as taken down: a rebuild is
        # not an outage, and reporting it as one would send an operator
        # looking for a subsystem that is up.
        assert report.activated == ("memory", "knowledge")
        assert report.deactivated == ()

    async def test_a_provider_losing_its_own_requirement_takes_consumers_too(
        self,
    ) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        events: list[str] = []
        reconciler = SubsystemReconciler(
            self._pair(world, events), _all_capabilities(world)
        )
        state = self._state_reading({"memory.backend": "inmemory"})

        await reconciler.reconcile(state, trigger="boot")
        events.clear()

        world.present.discard(CapabilityId.PERSISTENCE)
        report = await reconciler.reconcile(state, trigger="resync")

        assert events == ["down:knowledge", "down:memory"]
        assert report.deactivated == ("knowledge", "memory")


@pytest.mark.unit
class TestDeclinedRetry:
    """A decline is a verdict on its inputs, so unchanged inputs cost nothing.

    Without this, every event trigger re-runs the whole wiring tree for each
    subsystem that declined, to reach the same refusal. On a deployment with
    an unset embedding model that is twenty activations per pass.
    """

    async def test_an_event_trigger_does_not_re_run_a_decline(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        await reconciler.reconcile(state, trigger="boot")
        for _ in range(5):
            await reconciler.reconcile(state, trigger="settings_write")
        await reconciler.reconcile(state, trigger="provider_mutation")

        # Re-running wiring that read the same inputs declines the same way.
        # Every trigger paying for that turns a burst of unrelated writes into
        # a burst of full re-wiring.
        assert attempts == [1]

    async def test_a_requirement_arriving_re_runs_the_decline(self) -> None:
        world = _World()
        attempts: list[int] = []

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        await reconciler.reconcile(state, trigger="boot")
        # Nothing was attempted: the requirement is missing, so the subsystem
        # is waiting rather than declining.
        assert attempts == []

        # The requirement arrives. What the subsystem would read has moved, so
        # the snapshot must not suppress the attempt.
        world.present.add(CapabilityId.PERSISTENCE)
        await reconciler.reconcile(state, trigger="settings_write")
        assert attempts == [1]

        await reconciler.reconcile(state, trigger="settings_write")
        assert attempts == [1]

    async def test_a_rebuilt_dependency_re_runs_it_on_an_event_trigger(self) -> None:
        world = _World()
        attempts: list[int] = []

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        owner = SubsystemSpec(
            name="persistence",
            provides=CapabilityId.PERSISTENCE,
            activate=_installs(world, "persistence", CapabilityId.PERSISTENCE),
            deactivate=_removes(world, "persistence", CapabilityId.PERSISTENCE),
        )
        consumer = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((owner, consumer), _all_capabilities(world))
        state = _app_state()

        await reconciler.reconcile(state, trigger="boot")
        await reconciler.reconcile(state, trigger="settings_write")

        # The owner goes and comes back, so the consumer would be reading a
        # different instance than the one it declined over. Availability alone
        # cannot see that; the generation counter can.
        world.present.discard(CapabilityId.PERSISTENCE)
        await reconciler.reconcile(state, trigger="settings_write")

        assert attempts == [1, 1]

    async def test_a_declared_setting_changing_re_runs_it(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []
        values = {"memory.backend": "inmemory"}

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
            settings=("memory.backend",),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        await reconciler.reconcile(state, trigger="settings_write")
        # The operator changes the value the subsystem was waiting on, so the
        # attempt is worth making again on that same write rather than at the
        # next sweep.
        values["memory.backend"] = "sqlvector"
        await reconciler.reconcile(state, trigger="settings_write")

        assert attempts == [1, 1]

    async def test_an_unreadable_setting_does_not_read_as_a_change(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        async def _get_str(namespace: str, key: str) -> str:
            msg = f"settings backend is down for {namespace}.{key}"
            raise ConnectionError(msg)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
            settings=("memory.backend",),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        await reconciler.reconcile(state, trigger="settings_write")
        await reconciler.reconcile(state, trigger="settings_write")

        # An unreadable key snapshots the same way every pass. Were it to
        # snapshot as something new each time, a resolver outage would present
        # as drift and re-run the whole wiring tree on every trigger.
        assert attempts == [1]

    async def test_a_transient_read_failure_does_not_rebuild_what_is_up(self) -> None:
        # The snapshot a live subsystem is compared against came from a
        # successful read. Were an unreadable key to compare as a value, one
        # resolver hiccup would tear down every rebuild_on_change subsystem at
        # once: memory, and everything that captured it.
        world = _World(CapabilityId.PERSISTENCE)
        readable = True
        values = {"memory.backend": "inmemory"}

        async def _get_str(namespace: str, key: str) -> str:
            if not readable:
                msg = f"settings backend is down for {namespace}.{key}"
                raise ConnectionError(msg)
            return values[f"{namespace}.{key}"]

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        readable = False
        outage = await reconciler.reconcile(state, trigger="settings_write")
        assert outage.activated == ()
        assert outage.statuses[0].phase is SubsystemPhase.ACTIVE

        # The value the subsystem baked in really did change while the
        # resolver was unreadable, so recovery must still catch it. A rebuild
        # is teardown-then-activate, so it reports as activated.
        readable = True
        values["memory.backend"] = "sqlvector"
        recovered = await reconciler.reconcile(state, trigger="settings_write")
        assert recovered.activated == ("memory",)

    async def test_a_setting_unreadable_at_activation_is_adopted_on_recovery(
        self,
    ) -> None:
        # The snapshot taken at activation holds no reading for the key, and a
        # position with no reading is skipped. Without adopting the first
        # actual reading as the baseline, that key would stay uncomparable and
        # the rebuild it was declared for could never fire again.
        world = _World(CapabilityId.PERSISTENCE)
        readable = False
        values = {"memory.backend": "inmemory"}

        async def _get_str(namespace: str, key: str) -> str:
            if not readable:
                msg = f"settings backend is down for {namespace}.{key}"
                raise ConnectionError(msg)
            return values[f"{namespace}.{key}"]

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        readable = True
        adopted = await reconciler.reconcile(state, trigger="settings_write")
        assert adopted.activated == ()

        values["memory.backend"] = "sqlvector"
        rebuilt = await reconciler.reconcile(state, trigger="settings_write")
        assert rebuilt.activated == ("memory",)

    async def test_a_raising_probe_reads_as_absence_and_names_the_capability(
        self,
    ) -> None:
        world = _World()

        def _explode(_state: AppState) -> bool:
            msg = "probe read a slice that is not there"
            raise AttributeError(msg)

        capabilities = tuple(
            Capability(id=cap_id, present=_explode)
            if cap_id is CapabilityId.PERSISTENCE
            else _capability(world, cap_id)
            for cap_id in CapabilityId
        )
        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), capabilities)
        state = _app_state()

        report = await reconciler.reconcile(state, trigger="boot")

        # The pass survives the fault rather than aborting every other
        # subsystem over it, and the requirement it could not read is named:
        # waiting with an empty waiting_on would leave an operator nowhere to
        # look. Not activated, because a probe that cannot answer is not a
        # licence to wire against the dependency it was asked about.
        (status,) = report.statuses
        assert status.phase is SubsystemPhase.WAITING
        assert status.waiting_on == (CapabilityId.PERSISTENCE,)
        assert world.activations == []

    async def test_the_sweep_re_runs_a_decline_with_nothing_changed(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []

        async def _decline(_state: AppState) -> None:
            attempts.append(1)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        await reconciler.reconcile(state, trigger="boot")
        await reconciler.reconcile(state, trigger="resync", retry_declined=True)

        # Nothing declared moved, and the sweep tries anyway: a decline over a
        # condition the declaration does not model would otherwise be
        # permanent.
        assert attempts == [1, 1]

    async def test_a_teardown_restores_the_next_attempt(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        attempts: list[int] = []
        allow = {"now": True}

        async def _activate(_state: AppState) -> None:
            attempts.append(1)
            if allow["now"]:
                world.present.add(CapabilityId.MEMORY_BACKEND)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_activate,
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()

        await reconciler.reconcile(state, trigger="boot")
        assert attempts == [1]

        # Losing the requirement tears it down; regaining it must activate
        # again rather than compare against a snapshot the teardown
        # invalidated.
        allow["now"] = False
        world.present.discard(CapabilityId.PERSISTENCE)
        await reconciler.reconcile(state, trigger="provider_mutation")
        world.present.add(CapabilityId.PERSISTENCE)
        await reconciler.reconcile(state, trigger="provider_mutation")

        assert attempts == [1, 1]


@pytest.mark.unit
class TestWhyItIsNotUp:
    """A phase an operator cannot act on is the same dead end as no answer.

    Each of these was a place the status surface knew the answer and said
    something weaker: a decline with no reason, a wait with no exit, and a
    teardown window reported as waiting on nothing at all.
    """

    async def test_a_declined_subsystem_names_the_setting_it_wanted(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)

        async def _decline(_state: AppState) -> None:
            """Read the settings and install nothing, as the memory wiring does."""

        async def _get_str(namespace: str, key: str) -> str:
            return {"memory.embedder_model": "", "memory.backend": "sqlvector"}[
                f"{namespace}.{key}"
            ]

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
            settings=("memory.backend", "memory.embedder_model"),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        report = await reconciler.reconcile(state, trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "memory")

        assert status.phase is SubsystemPhase.BLOCKED
        # The blank one and only the blank one: naming every declared setting
        # would put the operator back to reading them all to find the empty
        # one, which is the search the detail exists to remove.
        assert status.detail == "unset: memory.embedder_model"

    async def test_a_decline_with_nothing_declared_still_points_somewhere(
        self,
    ) -> None:
        # BLOCKED with no detail is the state an operator cannot act on, and
        # is what the status surface exists to remove. When the declarations
        # genuinely say nothing, saying so IS the reason, and it names the
        # one place the condition can be.
        world = _World(CapabilityId.PERSISTENCE)

        async def _decline(_state: AppState) -> None:
            """Install nothing, over a condition the declaration cannot see."""

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "memory")

        assert status.phase is SubsystemPhase.BLOCKED
        assert status.detail is not None
        assert "does not declare" in status.detail
        assert "memory" in status.detail

    async def test_an_activation_that_knows_why_is_believed(self) -> None:
        # The guess from declared settings is a fallback. An activation that
        # raises with its own reason is the one that actually knows, so its
        # message reaches the operator verbatim rather than being replaced by
        # a blank-setting inference that may name the wrong thing.
        world = _World(CapabilityId.PERSISTENCE)

        async def _decline(_state: AppState) -> None:
            """Refuse, naming the condition the declaration cannot express."""
            msg = "waiting on: the vector extension"
            raise SubsystemDeclinedError(msg)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            settings=("memory.embedder_model",),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "memory")

        assert status.phase is SubsystemPhase.BLOCKED
        assert status.detail == "waiting on: the vector extension"

    async def test_a_declined_activation_is_not_a_failure(self) -> None:
        # A subsystem that declined is correctly not up and will be retried;
        # reporting it FAILED would send an operator looking for a fault.
        world = _World(CapabilityId.PERSISTENCE)

        async def _decline(_state: AppState) -> None:
            """Refuse with a reason."""
            msg = "waiting on: an operator choice"
            raise SubsystemDeclinedError(msg)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_decline,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "memory")

        assert status.phase is SubsystemPhase.BLOCKED
        assert report.failed == ()

    async def test_a_reason_clears_when_the_subsystem_comes_up(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        values = {"memory.embedder_model": ""}

        async def _activate(_state: AppState) -> None:
            if values["memory.embedder_model"]:
                world.present.add(CapabilityId.MEMORY_BACKEND)

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_activate,
            settings=("memory.embedder_model",),
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        values["memory.embedder_model"] = "example-provider/example-basic-001"
        report = await reconciler.reconcile(state, trigger="settings_write")
        status = next(entry for entry in report.statuses if entry.name == "memory")

        # A reason surviving the fix is a stale field an operator acts on, so
        # it is dropped with the decline it explained rather than overwritten
        # on the next decline that happens to produce one.
        assert status.phase is SubsystemPhase.ACTIVE
        assert status.detail is None

    async def test_waiting_on_a_disabled_owner_is_unreachable(self) -> None:
        world = _World()
        owner = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            activate=_installs(world, "knowledge", CapabilityId.KNOWLEDGE_ENGINE),
            enabled_by="knowledge.enabled",
        )
        consumer = SubsystemSpec(
            name="brain",
            provides=CapabilityId.PROJECT_BRAIN,
            requires=(CapabilityId.KNOWLEDGE_ENGINE,),
            activate=_installs(world, "brain", CapabilityId.PROJECT_BRAIN),
        )
        reconciler = SubsystemReconciler((owner, consumer), _all_capabilities(world))
        config = RootConfig(company_name="test")
        state = AppState(
            config=config.model_copy(
                update={
                    "knowledge": config.knowledge.model_copy(update={"enabled": False})
                }
            )
        )

        report = await reconciler.reconcile(state, trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "brain")

        # Level-triggering rests on "absent at boot is not a verdict: the next
        # pass picks it up". Over an owner an operator switched off there is no
        # such pass, so WAITING would be a promise the reconciler cannot keep.
        assert status.phase is SubsystemPhase.UNREACHABLE
        assert status.waiting_on == (CapabilityId.KNOWLEDGE_ENGINE,)
        assert status.detail is not None
        assert "knowledge" in status.detail

    async def test_waiting_on_a_blocked_owner_is_unreachable(self) -> None:
        world = _World()

        async def _decline(_state: AppState) -> None:
            """Install nothing, so the owner rests BLOCKED."""

        owner = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            activate=_decline,
        )
        consumer = SubsystemSpec(
            name="brain",
            provides=CapabilityId.PROJECT_BRAIN,
            requires=(CapabilityId.KNOWLEDGE_ENGINE,),
            activate=_installs(world, "brain", CapabilityId.PROJECT_BRAIN),
        )
        reconciler = SubsystemReconciler((owner, consumer), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "brain")

        assert status.phase is SubsystemPhase.UNREACHABLE
        assert status.detail is not None
        assert "knowledge" in status.detail

    async def test_waiting_on_a_late_owner_stays_waiting(self) -> None:
        # The case UNREACHABLE must not swallow: an owner that has not run yet
        # is exactly the one the next pass brings up, and reporting it as
        # unreachable would send an operator hunting a setting to change.
        world = _World()
        consumer = SubsystemSpec(
            name="brain",
            provides=CapabilityId.PROJECT_BRAIN,
            requires=(CapabilityId.KNOWLEDGE_ENGINE,),
            activate=_installs(world, "brain", CapabilityId.PROJECT_BRAIN),
        )
        reconciler = SubsystemReconciler((consumer,), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "brain")

        assert status.phase is SubsystemPhase.WAITING
        assert status.waiting_on == (CapabilityId.KNOWLEDGE_ENGINE,)
        assert status.detail is None

    async def test_one_stuck_owner_among_late_ones_is_still_unreachable(
        self,
    ) -> None:
        # The two verdicts differ in what the operator must do (change
        # something vs wait), so a subsystem waiting on both has to report the
        # one that needs action. Reporting WAITING because most owners are
        # merely late is how a stuck dependency hides in a crowd.
        world = _World()

        async def _decline(_state: AppState) -> None:
            """Install nothing, so this owner rests BLOCKED."""

        stuck = SubsystemSpec(
            name="knowledge",
            provides=CapabilityId.KNOWLEDGE_ENGINE,
            activate=_decline,
        )
        consumer = SubsystemSpec(
            name="brain",
            provides=CapabilityId.PROJECT_BRAIN,
            # MEMORY_BACKEND has no spec at all: nothing has run for it yet,
            # which is the ordinary late case.
            requires=(CapabilityId.KNOWLEDGE_ENGINE, CapabilityId.MEMORY_BACKEND),
            activate=_installs(world, "brain", CapabilityId.PROJECT_BRAIN),
        )
        reconciler = SubsystemReconciler((stuck, consumer), _all_capabilities(world))

        report = await reconciler.reconcile(_app_state(), trigger="boot")
        status = next(entry for entry in report.statuses if entry.name == "brain")

        assert status.phase is SubsystemPhase.UNREACHABLE
        # Both are still named: the operator needs the whole gap, not only
        # the part that needs a change.
        assert set(status.waiting_on) == {
            CapabilityId.KNOWLEDGE_ENGINE,
            CapabilityId.MEMORY_BACKEND,
        }
        assert status.detail is not None
        assert "knowledge" in status.detail

    async def test_a_mid_rebuild_read_reports_rebuilding(self) -> None:
        world = _World(CapabilityId.PERSISTENCE)
        values = {"memory.backend": "inmemory"}
        seen: list[SubsystemPhase] = []
        state = _app_state()

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        async def _activate(_state: AppState) -> None:
            world.present.add(CapabilityId.MEMORY_BACKEND)

        async def _deactivate(_state: AppState) -> None:
            # Reading from inside the teardown is the window a concurrent
            # GET /subsystems lands in. Before REBUILDING existed it answered
            # WAITING with an empty waiting_on: the contract's own shape for
            # "these are missing" used to name none of them.
            world.present.discard(CapabilityId.MEMORY_BACKEND)
            seen.extend(
                status.phase
                for status in reconciler.statuses(state)
                if status.name == "memory"
            )

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_activate,
            deactivate=_deactivate,
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        values["memory.backend"] = "sqlvector"
        report = await reconciler.reconcile(state, trigger="settings_write")

        assert seen == [SubsystemPhase.REBUILDING]
        # And the mark is scoped to the pass: the subsystem is up again by the
        # time the pass returns, so a later read must not still see it.
        status = next(entry for entry in report.statuses if entry.name == "memory")
        assert status.phase is SubsystemPhase.ACTIVE
        assert reconciler.statuses(state)[0].phase is SubsystemPhase.ACTIVE

    async def test_a_rebuild_marks_the_followers_it_takes_down_with_it(
        self,
    ) -> None:
        # A rebuild tears down everything reading through the subsystem, so a
        # follower is down for the same window and for the same reason. Left
        # unmarked it answers WAITING with an empty waiting_on, which is the
        # shape this phase exists to replace, one level removed from the
        # subsystem the operator actually changed.
        world = _World(CapabilityId.PERSISTENCE)
        values = {"memory.backend": "inmemory"}
        seen: dict[str, SubsystemPhase] = {}
        state = _app_state()

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        async def _activate_owner(_state: AppState) -> None:
            world.present.add(CapabilityId.MEMORY_BACKEND)

        async def _deactivate_owner(_state: AppState) -> None:
            world.present.discard(CapabilityId.MEMORY_BACKEND)
            seen.update(
                {status.name: status.phase for status in reconciler.statuses(state)}
            )

        async def _deactivate_follower(_state: AppState) -> None:
            world.present.discard(CapabilityId.PROJECT_BRAIN)

        owner = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_activate_owner,
            deactivate=_deactivate_owner,
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        follower = SubsystemSpec(
            name="brain",
            provides=CapabilityId.PROJECT_BRAIN,
            requires=(CapabilityId.MEMORY_BACKEND,),
            activate=_installs(world, "brain", CapabilityId.PROJECT_BRAIN),
            deactivate=_deactivate_follower,
            # Required by the graph: a consumer of a replaceable capability
            # must be replaceable itself, or it keeps the instance that was
            # replaced. That is the same rule that makes it a follower here.
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((owner, follower), _all_capabilities(world))
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        values["memory.backend"] = "sqlvector"
        report = await reconciler.reconcile(state, trigger="settings_write")

        assert seen["memory"] is SubsystemPhase.REBUILDING
        assert seen["brain"] is SubsystemPhase.REBUILDING
        # Both come back inside the same pass, so neither keeps the mark.
        assert {entry.name: entry.phase for entry in report.statuses} == {
            "memory": SubsystemPhase.ACTIVE,
            "brain": SubsystemPhase.ACTIVE,
        }

    async def test_a_failed_rebuild_does_not_leave_the_mark_behind(self) -> None:
        # REBUILDING promises "coming back inside this pass". An activation
        # that raises breaks that promise, and a mark surviving the pass would
        # report a permanently failed subsystem as mid-rebuild forever.
        world = _World(CapabilityId.PERSISTENCE)
        values = {"memory.backend": "inmemory"}
        attempts: list[int] = []

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        async def _activate(_state: AppState) -> None:
            attempts.append(1)
            if len(attempts) > 1:
                msg = "rebuild failed"
                raise SubsystemGraphInvalidError(msg)
            world.present.add(CapabilityId.MEMORY_BACKEND)

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_activate,
            deactivate=_removes(world, "memory", CapabilityId.MEMORY_BACKEND),
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        values["memory.backend"] = "sqlvector"
        report = await reconciler.reconcile(state, trigger="settings_write")

        status = next(entry for entry in report.statuses if entry.name == "memory")
        assert status.phase is SubsystemPhase.FAILED
        assert reconciler.statuses(state)[0].phase is SubsystemPhase.FAILED

    async def test_a_pass_that_raises_does_not_leave_the_mark_behind(self) -> None:
        # The mark is cleared at both ends of a pass, so only a raise between
        # them can strand it, and a teardown is where one can: ``_deactivate``
        # records every ordinary failure and carries on, but re-raises an
        # interpreter-level critical, which leaves the pass mid-rebuild. A
        # stranded mark reports a real outage as REBUILDING until some later
        # pass happens to clear it, which is the one reading that tells an
        # operator to wait rather than look.
        world = _World(CapabilityId.PERSISTENCE)
        values = {"memory.backend": "inmemory"}

        async def _get_str(namespace: str, key: str) -> str:
            return values[f"{namespace}.{key}"]

        async def _critical_teardown(_state: AppState) -> None:
            raise MemoryError

        spec = SubsystemSpec(
            name="memory",
            provides=CapabilityId.MEMORY_BACKEND,
            requires=(CapabilityId.PERSISTENCE,),
            activate=_installs(world, "memory", CapabilityId.MEMORY_BACKEND),
            deactivate=_critical_teardown,
            settings=("memory.backend",),
            rebuild_on_change=True,
        )
        reconciler = SubsystemReconciler((spec,), _all_capabilities(world))
        state = _app_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=mock_of[ConfigResolver](get_str=_get_str),
        )

        await reconciler.reconcile(state, trigger="boot")
        values["memory.backend"] = "sqlvector"
        with pytest.raises(MemoryError):
            await reconciler.reconcile(state, trigger="settings_write")

        assert all(
            status.phase is not SubsystemPhase.REBUILDING
            for status in reconciler.statuses(state)
        )


@pytest.mark.unit
class TestReconcileEntryPoint:
    """What the one call boot and every trigger share does with a fault."""

    async def test_an_invalid_declaration_graph_reaches_the_caller(self) -> None:
        # A duplicate provider is a defect in the shipped declarations, so
        # every later pass raises the same thing. Reporting it as the ``None``
        # a transient fault returns would leave the whole system unwired
        # behind a log line, with each trigger dutifully logging it again.
        world = _World()
        duplicated = tuple(
            SubsystemSpec(
                name=name,
                provides=CapabilityId.MEMORY_BACKEND,
                activate=_installs(world, name, CapabilityId.MEMORY_BACKEND),
            )
            for name in ("one", "two")
        )
        with (
            patch("synthorg.api.subsystems.runtime.SUBSYSTEMS", duplicated),
            pytest.raises(SubsystemGraphInvalidError, match="one owner"),
        ):
            await reconcile_subsystems(_app_state(), trigger="test")
