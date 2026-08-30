"""Conformance for the shipped subsystem declarations.

The reconciler is only as good as what it is handed, and a bad declaration is
silent: a subsystem whose capability nothing probes never activates, and a
setting no subsystem names never reaches the subscriber that would trigger a
pass. These assert the shipped table against the shipped probes.
"""

import pytest

from synthorg.api.subsystems.capabilities import CAPABILITIES
from synthorg.api.subsystems.graph import order_subsystems
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.settings.subscribers.subsystem_reconcile_subscriber import (
    SubsystemReconcileSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_PROBED = {capability.id for capability in CAPABILITIES}
_IDS = [spec.name for spec in SUBSYSTEMS]


def _declared_settings(spec: SubsystemSpec) -> tuple[str, ...]:
    """Return every ``namespace.key`` a spec names.

    Returns:
        The declared settings plus the gate key, when it has one.
    """
    gate = (spec.enabled_by,) if spec.enabled_by is not None else ()
    return (*spec.settings, *gate)


def test_the_shipped_declarations_order() -> None:
    """No cycle, no capability with two owners, no rebuild without a teardown."""
    # Ordered against the shipped probes, as the reconciler does. Handing it
    # no probes would make every ambient requirement read as unowned, which
    # is a different assertion than this one.
    assert len(order_subsystems(SUBSYSTEMS, _PROBED)) == len(SUBSYSTEMS)


@pytest.mark.parametrize("spec", SUBSYSTEMS, ids=_IDS)
def test_every_declared_capability_is_probed(spec: SubsystemSpec) -> None:
    """A capability nothing probes reads absent forever, so it never comes up."""
    assert spec.provides in _PROBED, f"{spec.name} provides an unprobed capability"
    unprobed = [need for need in spec.requires if need not in _PROBED]
    assert unprobed == [], f"{spec.name} requires unprobed: {unprobed}"


@pytest.mark.parametrize("spec", SUBSYSTEMS, ids=_IDS)
def test_every_declared_setting_is_registered(spec: SubsystemSpec) -> None:
    """A declared key that is not a real setting can never resolve or trigger."""
    for entry in _declared_settings(spec):
        namespace, _, key = entry.partition(".")
        assert get_registry().get(namespace, key) is not None, (
            f"{spec.name} declares unregistered setting {entry}"
        )


def _spec(name: str) -> SubsystemSpec:
    """Return the shipped spec called *name*.

    Returns:
        The declaration, so a rename fails here rather than silently skipping.
    """
    for spec in SUBSYSTEMS:
        if spec.name == name:
            return spec
    msg = f"no subsystem declared as {name!r}"
    raise AssertionError(msg)


def test_each_tail_stage_waits_for_its_own_dependency() -> None:
    """Each tail collaborator declares what IT needs, and nothing more.

    They need different things: the work pipeline (integrate), the provider
    registry (evaluate), the coordinator (replan), the memory layers (retro
    capture). None exist when persistence and the task engine do, so a tail
    declared to need only those two activates into a rollup whose every stage
    declined, reads as converged, and is never revisited.

    Declaring the union instead makes one absent collaborator hold back the
    others, which is what left a coordinator-less boot with no integrate stage
    either, against the degradation table in docs/design/initiative-tail.md.
    """
    integrate = set(_spec("initiative_integrate").requires)
    evaluate = set(_spec("initiative_evaluate").requires)
    replan = set(_spec("initiative_replan").requires)
    retro = set(_spec("initiative_retro_capture").requires)

    assert CapabilityId.WORK_PIPELINE in integrate
    assert CapabilityId.PROVIDER_REGISTRY in evaluate
    assert CapabilityId.COORDINATOR in replan
    assert {CapabilityId.MEMORY_BACKEND, CapabilityId.ORG_MEMORY_BACKEND} <= retro

    assert CapabilityId.COORDINATOR not in integrate | evaluate | retro
    assert CapabilityId.WORK_PIPELINE not in evaluate | replan | retro
    assert not {CapabilityId.MEMORY_BACKEND, CapabilityId.ORG_MEMORY_BACKEND} & (
        integrate | evaluate | replan
    )


def test_the_charter_approve_path_is_its_own_subsystem() -> None:
    """Approving a charter waits for the work pipeline; interviewing does not.

    The interview needs a provider and persistence, both of which exist early;
    the dispatcher additionally needs the work pipeline, the forecast store and
    the budget config, which arrive with the runtime services seconds later.
    Folding the two into one activation strands the approve path for the life
    of the process, because the activation's own idempotency guard reads the
    interview service and every later pass then returns before reaching the
    dispatcher. A live run met that as a 503 on the first approval it tried.
    """
    engine = _spec("charter_engine")
    dispatch = _spec("charter_dispatch")

    assert CapabilityId.WORK_PIPELINE not in engine.requires
    assert dispatch.provides is CapabilityId.CHARTER_DISPATCH
    assert CapabilityId.WORK_PIPELINE in dispatch.requires
    assert CapabilityId.CHARTER_ENGINE in dispatch.requires


def test_the_rollup_does_not_wait_for_the_tail_it_carries() -> None:
    """The rollup is useful tailless, so it must not be held back by the tail.

    It derives plan status, walks the project, and advances the objective task
    with no provider anywhere. Widening its own requirements to cover the tail
    would take all of that away for the whole window before setup completes.
    """
    requires = set(_spec("project_rollup_service").requires)

    assert requires == {
        CapabilityId.PERSISTENCE,
        CapabilityId.TASK_ENGINE,
        CapabilityId.SETTINGS_RESOLVER,
    }


def test_the_subscriber_watches_every_declared_setting() -> None:
    """The watched set is derived, so it cannot fall behind the declarations."""
    declared = {
        (entry.partition(".")[0], entry.partition(".")[2])
        for spec in SUBSYSTEMS
        for entry in _declared_settings(spec)
    }
    subscriber = SubsystemReconcileSettingsSubscriber(
        app_state=make_app_state(),
        settings_service=mock_of[SettingsService](),
    )
    assert declared <= subscriber.watched_keys
