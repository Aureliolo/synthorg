"""Governance tests for the controls this project made live.

Each key here was previously fixed at process start, so a write was rejected
outright and there was nothing to guard. Once the value applies while the
system runs, relaxing it is one ordinary write, and these pin the direction
that needs a deliberate confirm + reason + actor.
"""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit

_SATISFIED = SettingsWriteGovernance(confirm=True, reason="ops", actor="admin")


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


async def _guarded(item: tuple[str, str, str], current: str | None) -> bool:
    """Report whether the write is refused without governance.

    Returns:
        ``True`` when the guardrail rejects the unauthorised write.
    """
    try:
        await enforce_security_write_governance(
            [item], governance=None, get_current=_current(current)
        )
    except SecurityToggleConfirmationRequiredError:
        return True
    return False


class TestGlobalRateLimiter:
    async def test_disabling_the_limiter_is_guarded(self) -> None:
        assert await _guarded(("api", "rate_limiter_enabled", "false"), "true")

    async def test_disabling_from_unset_is_guarded(self) -> None:
        # Unset resolves to the registered "true", so the first explicit
        # disable is still a weakening rather than an unknowable transition.
        assert await _guarded(("api", "rate_limiter_enabled", "false"), None)

    async def test_enabling_the_limiter_is_unguarded(self) -> None:
        assert not await _guarded(("api", "rate_limiter_enabled", "true"), "false")

    async def test_disabling_with_governance_is_allowed(self) -> None:
        await enforce_security_write_governance(
            [("api", "rate_limiter_enabled", "false")],
            governance=_SATISFIED,
            get_current=_current("true"),
        )

    @pytest.mark.parametrize(
        "key",
        [
            "rate_limit_floor_max_requests",
            "rate_limit_unauth_max_requests",
            "rate_limit_auth_max_requests",
            "rate_limit_auth_endpoint_max_requests",
        ],
    )
    async def test_raising_a_tier_budget_is_guarded(self, key: str) -> None:
        assert await _guarded(("api", key, "999999"), "10")

    @pytest.mark.parametrize(
        "key",
        [
            "rate_limit_floor_max_requests",
            "rate_limit_unauth_max_requests",
            "rate_limit_auth_max_requests",
            "rate_limit_auth_endpoint_max_requests",
        ],
    )
    async def test_lowering_a_tier_budget_is_unguarded(self, key: str) -> None:
        assert not await _guarded(("api", key, "1"), "10")

    async def test_raising_the_login_budget_from_its_default_is_guarded(self) -> None:
        # The brute-force bound on the credential routes: unset resolves to the
        # registered 10, so raising it without a stored value is still guarded.
        assert await _guarded(
            ("api", "rate_limit_auth_endpoint_max_requests", "1000"), None
        )

    async def test_shortening_the_window_is_guarded(self) -> None:
        # The same cap over a second instead of a minute admits sixty times
        # the traffic.
        assert await _guarded(("api", "rate_limit_time_unit", "second"), "minute")

    async def test_lengthening_the_window_is_unguarded(self) -> None:
        assert not await _guarded(("api", "rate_limit_time_unit", "hour"), "minute")

    async def test_an_unparseable_budget_is_not_treated_as_weakening(self) -> None:
        # The type validator rejects it downstream; the guard must not read a
        # malformed value as a deliberate relaxation.
        assert not await _guarded(("api", "rate_limit_auth_max_requests", "lots"), "10")


class TestAuthTokenEntropy:
    async def test_narrowing_the_token_width_is_guarded(self) -> None:
        assert await _guarded(("security", "auth_token_bytes", "16"), "32")

    async def test_widening_the_token_width_is_unguarded(self) -> None:
        assert not await _guarded(("security", "auth_token_bytes", "64"), "32")

    async def test_narrowing_from_unset_is_guarded(self) -> None:
        assert await _guarded(("security", "auth_token_bytes", "16"), None)


class TestAgentMiddlewareChain:
    async def test_dropping_the_chain_is_guarded(self) -> None:
        # The chain carries the authority-deference defence, so turning it off
        # removes a prompt-injection countermeasure.
        assert await _guarded(("engine", "enable_agent_middleware", "false"), "true")

    async def test_dropping_from_unset_is_guarded(self) -> None:
        assert await _guarded(("engine", "enable_agent_middleware", "false"), None)

    async def test_restoring_the_chain_is_unguarded(self) -> None:
        assert not await _guarded(
            ("engine", "enable_agent_middleware", "true"), "false"
        )


class TestSelfModification:
    async def test_enabling_code_modification_is_guarded(self) -> None:
        assert await _guarded(
            ("self_improvement", "code_modification_enabled", "true"), "false"
        )

    async def test_enabling_from_unset_is_guarded(self) -> None:
        assert await _guarded(
            ("self_improvement", "code_modification_enabled", "true"), None
        )

    async def test_disabling_code_modification_is_unguarded(self) -> None:
        assert not await _guarded(
            ("self_improvement", "code_modification_enabled", "false"), "true"
        )
