"""Tests for the level-triggered subsystem reconciler.

The properties under test are the ones the pattern rests on: a pass is
idempotent, a dependency that arrives late still brings its dependents up,
and a subsystem that cannot activate is recorded rather than allowed to
abort the pass or to be forgotten.
"""

from unittest.mock import patch

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
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

        # The operator configures the embedder. Nothing announces it, which
        # is exactly why the sweep keeps asking, and why it is the one caller
        # that asks unconditionally.
        allow["now"] = True
        later = await reconciler.reconcile(state, trigger="resync", retry_declined=True)

        assert later.activated == ("memory",)
        assert later.statuses[0].phase is SubsystemPhase.ACTIVE


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
        await reconciler.reconcile(state, trigger="settings_write")
        await reconciler.reconcile(state, trigger="provider_mutation")

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

        # The condition that declined is one the declaration cannot model, so
        # nothing it could compare against would ever move. Only a caller that
        # knows time has passed can recover it.
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
