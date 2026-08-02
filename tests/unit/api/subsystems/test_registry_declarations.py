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
from synthorg.api.subsystems.spec import SubsystemSpec
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
