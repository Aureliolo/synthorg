"""What the risk-override subsystem tells an operator when it declines.

`GET /subsystems` exists to answer "why is this not up", so the reason is an
operator surface and is held to the same rules as any other. A live run read
this one as an instruction to restart the product for a configuration change,
which is the workflow the Compose-Set-Or-Live rule says does not exist. The
value is a deployment input rather than a setting, and saying which is the
difference between an operator changing it and an operator hunting Settings for
a key that was never there.
"""

from typing import cast

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.security_wiring import wire_risk_override_service
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.security.state import SecurityStateSlice
from synthorg.security.timeout.config import WaitForeverConfig
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state() -> AppState:
    return make_app_state(
        slices={
            PersistenceStateSlice: {"backend": object()},
            ApprovalStateSlice: {"store": ApprovalStore()},
            SecurityStateSlice: {"risk_override_service": None},
        },
    )


async def _decline_reason() -> str:
    with pytest.raises(SubsystemDeclinedError) as exc_info:
        await wire_risk_override_service(
            _app_state(),
            approval_timeout_config=WaitForeverConfig(),
            approval_timeout_scheduler=cast(
                "ApprovalTimeoutScheduler", mock_of[ApprovalTimeoutScheduler]()
            ),
        )
    return str(exc_info.value)


async def test_names_the_field_and_the_value_it_found() -> None:
    reason = await _decline_reason()

    assert "approval_timeout.policy" in reason
    assert "'wait'" in reason


async def test_does_not_tell_the_operator_to_restart() -> None:
    # There is no restart control and no pending-restart state in this
    # product, so a reason that asks for one describes a workflow the operator
    # does not have.
    assert "restart" not in (await _decline_reason()).lower()


async def test_says_it_is_not_a_setting() -> None:
    # The complement of the above, and the half that is actually actionable:
    # without it an operator reads "change it" and goes looking in Settings.
    assert "rather than a setting" in await _decline_reason()
