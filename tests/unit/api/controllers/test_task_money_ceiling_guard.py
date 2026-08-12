"""The per-task money ceiling is refused at the same door as the global one.

``Task.hard_ceiling`` overrides ``budget.run_hard_ceiling`` whenever it is
set, so an estate that cannot bind the setting cannot bind the task field
either. Guarding only the setting would leave the stricter, more specific
value as the unguarded one, which is the shape these tests pin.
"""

from collections.abc import Mapping
from typing import Final
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._task_money_ceiling import guard_task_money_ceiling
from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.core.domain_errors import ValidationError
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_TASK_ID: Final[str] = "task-budget"
_POSITIVE_CEILING: Final[float] = 25.0

#: Set by :func:`_app_state`, read by the resolver seam below. The guard takes
#: a real ``AppState`` (typeguard checks the annotation at runtime), so the
#: stand-in resolver cannot ride on the state object itself.
_RESOLVERS: dict[int, AsyncMock] = {}


def _configs(**models: BillingModel) -> Mapping[str, ProviderConfig]:
    return {
        name: ProviderConfig(
            driver="scripted",
            connection_name=f"conn-{name}",
            billing_model=model,
        )
        for name, model in models.items()
    }


def _app_state(configs: Mapping[str, ProviderConfig]) -> AppState:
    """Build an ``AppState`` whose config resolver reports *configs*.

    Returns:
        The state, with its stand-in resolver registered for the seam.
    """
    resolver = AsyncMock()
    resolver.get_provider_configs = AsyncMock(return_value=configs)
    state = make_app_state()
    _RESOLVERS[id(state)] = resolver
    return state


def _resolver_for(app_state: AppState) -> AsyncMock:
    """Return the stand-in resolver registered for *app_state*.

    Returns:
        The resolver :func:`_app_state` registered.
    """
    return _RESOLVERS[id(app_state)]


@pytest.fixture(autouse=True)
def _resolver_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the guard's resolver lookup at the stand-in above.

    The real accessor wants a settings backend wired through the state slice,
    which is more app than this unit needs; the guard's own decision is what
    is under test.
    """
    monkeypatch.setattr(
        "synthorg.api.controllers._task_money_ceiling.config_resolver_of",
        _resolver_for,
    )


class TestTaskMoneyCeilingGuard:
    async def test_a_flat_rate_estate_refuses_the_task_ceiling(self) -> None:
        state = _app_state(_configs(sub=BillingModel.FLAT_RATE))

        with pytest.raises(ValidationError, match="cannot bind"):
            await guard_task_money_ceiling(
                state,
                {"hard_ceiling": _POSITIVE_CEILING},
                task_id=_TASK_ID,
            )

    async def test_the_refusal_names_the_bound_that_does_apply(self) -> None:
        # A refusal that only says no leaves the operator with a parked run
        # and no exit; the token ceiling is the one that measures this estate.
        state = _app_state(_configs(sub=BillingModel.FLAT_RATE))

        with pytest.raises(ValidationError, match="hard_token_ceiling"):
            await guard_task_money_ceiling(
                state,
                {"hard_ceiling": _POSITIVE_CEILING},
                task_id=_TASK_ID,
            )

    async def test_one_metered_connection_is_enough_to_bind(self) -> None:
        state = _app_state(
            _configs(sub=BillingModel.FLAT_RATE, metered=BillingModel.PER_TOKEN)
        )

        await guard_task_money_ceiling(
            state,
            {"hard_ceiling": _POSITIVE_CEILING},
            task_id=_TASK_ID,
        )

    async def test_an_unknown_connection_does_not_bind(self) -> None:
        # UNKNOWN is the honest answer for an undeclared connection, and it is
        # unmeasurable rather than assumed per-token: a ceiling believed to
        # bind when it may not is the failure this guard exists for.
        state = _app_state(_configs(mystery=BillingModel.UNKNOWN))

        with pytest.raises(ValidationError, match="cannot bind"):
            await guard_task_money_ceiling(
                state,
                {"hard_ceiling": _POSITIVE_CEILING},
                task_id=_TASK_ID,
            )

    async def test_zero_is_the_opt_out_and_is_never_refused(self) -> None:
        state = _app_state(_configs(sub=BillingModel.FLAT_RATE))

        await guard_task_money_ceiling(
            state,
            {"hard_ceiling": 0.0},
            task_id=_TASK_ID,
        )

    async def test_an_absent_field_is_not_judged(self) -> None:
        # An update that does not touch the ceiling changes nothing about
        # whether the existing one binds, and refusing it would make an
        # unrelated title edit fail on a flat-rate estate.
        state = _app_state(_configs(sub=BillingModel.FLAT_RATE))

        await guard_task_money_ceiling(
            state,
            {"title": "unrelated"},
            task_id=_TASK_ID,
        )
        _resolver_for(state).get_provider_configs.assert_not_awaited()

    async def test_an_estate_with_no_connections_is_not_refused(self) -> None:
        # No connection is no evidence either way, and refusing here would
        # make the operator's first connection unaddable over a bound they
        # set in the sensible order.
        state = _app_state({})

        await guard_task_money_ceiling(
            state,
            {"hard_ceiling": _POSITIVE_CEILING},
            task_id=_TASK_ID,
        )


def test_the_update_dto_carries_the_money_ceiling() -> None:
    """The documented knob has to exist, not merely be described.

    ``UpdateTaskRequest`` forbids extras, so a guide naming a field the DTO
    lacks documents an instruction that 422s. The enforcer reads
    ``Task.hard_ceiling`` whenever it is set, and this is the only door that
    sets it.
    """
    from synthorg.api.dto import UpdateTaskRequest

    request = UpdateTaskRequest(hard_ceiling=_POSITIVE_CEILING)

    assert request.hard_ceiling == _POSITIVE_CEILING
    # The controller drops unset fields, so an omitted ceiling must not reach
    # the guard or the engine as an explicit write.
    assert "hard_ceiling" not in UpdateTaskRequest().model_dump(exclude_none=True)
