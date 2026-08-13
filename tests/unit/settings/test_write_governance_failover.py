"""Governance tests for operator-declared failover.

Two keys, guarded in opposite directions from the default-on toggles next
door: the mechanism ships OFF, so the first stored ``true`` is what opens
it, and a route is guarded on ADDITION because the toggle alone cannot ask
again about a connection an operator makes reachable months later.
"""

import json
from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.registry import get_registry
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit

_SATISFIED = SettingsWriteGovernance(confirm=True, reason="ops", actor="admin")
_ENABLED = ("providers", "failover_enabled")
_ROUTES = ("providers", "failover_routes")

_DECLARED = "example-provider/example-expert-001"
_OTHER = "example-provider/example-basic-001"
_ALTERNATE = {"provider": "test-provider", "model_id": "example-capable-001"}
_ELSEWHERE = {"provider": "other-provider", "model_id": "example-capable-001"}


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


def _routes(**entries: dict[str, str]) -> str:
    return json.dumps(entries)


class TestMechanismToggle:
    def test_it_ships_off(self) -> None:
        # Every transition test below reads "unset means off" from the policy,
        # which is only true while the registry agrees.
        defn = get_registry().get(*_ENABLED)
        assert defn is not None
        assert defn.default == "false"

    async def test_enabling_from_unset_is_rejected(self) -> None:
        # The first write is what opens the mechanism, so it is the one that
        # has to be deliberate.
        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [(*_ENABLED, "true")], governance=None, get_current=_current(None)
            )

    async def test_enabling_with_governance_is_allowed(self) -> None:
        await enforce_security_write_governance(
            [(*_ENABLED, "true")], governance=_SATISFIED, get_current=_current(None)
        )

    async def test_disabling_is_unguarded(self) -> None:
        await enforce_security_write_governance(
            [(*_ENABLED, "false")], governance=None, get_current=_current("true")
        )

    async def test_restating_enabled_is_unguarded(self) -> None:
        await enforce_security_write_governance(
            [(*_ENABLED, "true")], governance=None, get_current=_current("true")
        )


class TestRoutes:
    async def test_declaring_the_first_route_is_rejected(self) -> None:
        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [(*_ROUTES, _routes(**{_DECLARED: _ALTERNATE}))],
                governance=None,
                get_current=_current(None),
            )

    async def test_adding_a_second_route_is_rejected(self) -> None:
        # A presence test would wave this through: the value already declared
        # a route, but a second pair now reaches a second connection.
        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [
                    (
                        *_ROUTES,
                        _routes(**{_DECLARED: _ALTERNATE, _OTHER: _ALTERNATE}),
                    )
                ],
                governance=None,
                get_current=_current(_routes(**{_DECLARED: _ALTERNATE})),
            )

    async def test_repointing_a_route_is_rejected(self) -> None:
        # The declared half is unchanged and the count is unchanged, but a
        # connection that could not serve this pair now can.
        with pytest.raises(SecurityToggleConfirmationRequiredError):
            await enforce_security_write_governance(
                [(*_ROUTES, _routes(**{_DECLARED: _ELSEWHERE}))],
                governance=None,
                get_current=_current(_routes(**{_DECLARED: _ALTERNATE})),
            )

    async def test_declaring_with_governance_is_allowed(self) -> None:
        await enforce_security_write_governance(
            [(*_ROUTES, _routes(**{_DECLARED: _ALTERNATE}))],
            governance=_SATISFIED,
            get_current=_current(None),
        )

    async def test_removing_a_route_is_unguarded(self) -> None:
        await enforce_security_write_governance(
            [(*_ROUTES, _routes(**{_DECLARED: _ALTERNATE}))],
            governance=None,
            get_current=_current(
                _routes(**{_DECLARED: _ALTERNATE, _OTHER: _ALTERNATE})
            ),
        )

    async def test_clearing_every_route_is_unguarded(self) -> None:
        await enforce_security_write_governance(
            [(*_ROUTES, "{}")],
            governance=None,
            get_current=_current(_routes(**{_DECLARED: _ALTERNATE})),
        )

    async def test_reordering_the_same_routes_is_unguarded(self) -> None:
        await enforce_security_write_governance(
            [(*_ROUTES, _routes(**{_OTHER: _ALTERNATE, _DECLARED: _ALTERNATE}))],
            governance=None,
            get_current=_current(
                _routes(**{_DECLARED: _ALTERNATE, _OTHER: _ALTERNATE})
            ),
        )

    async def test_a_malformed_value_is_not_read_as_a_widening(self) -> None:
        # The type validator rejects it downstream; reading it as a grant here
        # would fail the write with the wrong error.
        await enforce_security_write_governance(
            [(*_ROUTES, "{not json")],
            governance=None,
            get_current=_current(_routes(**{_DECLARED: _ALTERNATE})),
        )
