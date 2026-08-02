"""The one-shot callers refuse to read an unfinished pass as success.

The reconciler records a failing activation and carries on, which is right
for a periodic sweep. Setup completion is the opposite case: it is a
one-shot answer to "is this deployment configured", so a subsystem that
raised during the pass means the answer is no. Expected degradation
(a dependency simply absent) reads WAITING and must not block completion.

Boot holds the same rule for the same reason, so its gate is covered here
too: the fallback that follows it stands in for durable memory, and standing
in for a backend that was never tried is the failure both refuse.
"""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.api.controllers.setup._runtime_wiring import _reconcile_post_setup
from synthorg.api.lifecycle_runner_startup import (
    _reconcile_once_persistence_is_connected,
)
from synthorg.api.subsystems.errors import SubsystemActivationError
from synthorg.api.subsystems.report import ReconcileReport, SubsystemStatus
from synthorg.api.subsystems.runtime import reconcile_subsystems
from synthorg.api.subsystems.spec import CapabilityId, SubsystemPhase
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_RECONCILE = "synthorg.api.subsystems.runtime.reconcile_subsystems"


def _report(*statuses: SubsystemStatus) -> ReconcileReport:
    return ReconcileReport(statuses=statuses)


def _pass_returning(report: ReconcileReport | None) -> AsyncMock:
    return AsyncMock(spec=reconcile_subsystems, return_value=report)


async def test_a_converged_pass_completes_setup() -> None:
    report = _report(SubsystemStatus(name="memory", phase=SubsystemPhase.ACTIVE))
    with patch(_RECONCILE, _pass_returning(report)):
        await _reconcile_post_setup(make_app_state())


async def test_a_waiting_subsystem_does_not_block_completion() -> None:
    # A dependency that is simply absent is expected degradation, exactly as
    # at boot; the next pass picks it up once it arrives.
    report = _report(
        SubsystemStatus(
            name="charter",
            phase=SubsystemPhase.WAITING,
            waiting_on=(CapabilityId.PROVIDER_REGISTRY,),
        )
    )
    with patch(_RECONCILE, _pass_returning(report)):
        await _reconcile_post_setup(make_app_state())


async def test_a_failed_subsystem_refuses_completion() -> None:
    report = _report(
        SubsystemStatus(name="memory", phase=SubsystemPhase.ACTIVE),
        SubsystemStatus(name="docs_engine", phase=SubsystemPhase.FAILED, detail="boom"),
    )
    with (
        patch(_RECONCILE, _pass_returning(report)),
        pytest.raises(SubsystemActivationError, match="docs_engine"),
    ):
        await _reconcile_post_setup(make_app_state())


async def test_a_pass_that_could_not_run_refuses_completion() -> None:
    # ``None`` means the pass itself failed, so nothing is known about any
    # subsystem; claiming the deployment is configured would be a guess.
    with (
        patch(_RECONCILE, _pass_returning(None)),
        pytest.raises(SubsystemActivationError, match="could not run"),
    ):
        await _reconcile_post_setup(make_app_state())


async def test_a_deferred_pass_refuses_completion() -> None:
    # A deferred report is a snapshot of a pass running elsewhere: every
    # subsystem it has not reached yet still reads healthy, so an absent
    # failure here is not an answer to the question completion is asking.
    report = ReconcileReport(
        statuses=(SubsystemStatus(name="memory", phase=SubsystemPhase.ACTIVE),),
        deferred=True,
    )
    with (
        patch(_RECONCILE, _pass_returning(report)),
        pytest.raises(SubsystemActivationError, match="deferred"),
    ):
        await _reconcile_post_setup(make_app_state())


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (None, "could not run"),
        (
            ReconcileReport(
                statuses=(SubsystemStatus(name="memory", phase=SubsystemPhase.ACTIVE),),
                deferred=True,
            ),
            "deferred",
        ),
    ],
    ids=["never_ran", "deferred"],
)
async def test_boot_aborts_when_its_pass_did_not_run(
    report: ReconcileReport | None, expected: str
) -> None:
    # A subsystem that could not activate is a degrade boot carries on past.
    # A pass that never happened is not: the injected-store fallback below it
    # would then publish an ephemeral backend as though the durable one had
    # been given its chance.
    with (
        patch(_RECONCILE, _pass_returning(report)),
        pytest.raises(SubsystemActivationError, match=expected),
    ):
        await _reconcile_once_persistence_is_connected(make_app_state())
