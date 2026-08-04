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


def test_the_initiative_tail_waits_for_what_its_stages_need() -> None:
    """Each tail stage's own dependency is declared, so the tail waits for it.

    The three stages need the provider registry (evaluate), the work pipeline
    (integrate) and the coordinator (replan). None of them exist when
    persistence and the task engine do, so a tail declared to need only those
    two activates into a rollup whose every stage declined, reads as converged,
    and is never revisited: the initiative parks in the tail on every boot.
    """
    requires = set(_spec("initiative_tail").requires)

    assert CapabilityId.PROVIDER_REGISTRY in requires
    assert CapabilityId.WORK_PIPELINE in requires
    assert CapabilityId.COORDINATOR in requires


def test_the_rollup_does_not_wait_for_the_tail_it_carries() -> None:
    """The rollup is useful tailless, so it must not be held back by the tail.

    It derives plan status, walks the project, and advances the objective task
    with no provider anywhere. Widening its own requirements to cover the tail
    would take all of that away for the whole window before setup completes.
    """
    requires = set(_spec("project_rollup_service").requires)

    assert requires == {CapabilityId.PERSISTENCE, CapabilityId.TASK_ENGINE}


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
