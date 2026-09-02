"""Tests for the write-time rules that span two settings.

The rule under test is the rate-limit floor: it wraps every inner tier, so a
tier budget above it is a number an operator can set and the middleware stack
can never reach. Checked before anything is persisted, because a write that
lands is acknowledged, shown in the dashboard, and not enforced.
"""

from collections.abc import Awaitable, Callable, Mapping

import pytest

from synthorg.settings.cross_field_rules import enforce_cross_field_rules
from synthorg.settings.errors import SettingValidationError

pytestmark = pytest.mark.unit

_DEFAULTS = {
    ("api", "rate_limit_floor_max_requests"): "1000",
    ("api", "rate_limit_unauth_max_requests"): "20",
    ("api", "rate_limit_auth_max_requests"): "600",
    ("api", "rate_limit_auth_endpoint_max_requests"): "10",
    ("api", "rate_limit_time_unit"): "minute",
}


_GetCurrent = Callable[[str, str], Awaitable[str | None]]
_GetDefault = Callable[[str, str], str | None]


def _readers(
    stored: Mapping[tuple[str, str], str] | None = None,
    defaults: Mapping[tuple[str, str], str] = _DEFAULTS,
) -> tuple[_GetCurrent, _GetDefault]:
    """Build the current-value and default-value readers the rule takes.

    Returns:
        The ``(get_current, get_default)`` pair.
    """
    current = dict(stored or {})

    async def _get_current(namespace: str, key: str) -> str | None:
        return current.get((namespace, key))

    def _get_default(namespace: str, key: str) -> str | None:
        return defaults.get((namespace, key))

    return _get_current, _get_default


async def _enforce(
    items: list[tuple[str, str, str]],
    stored: Mapping[tuple[str, str], str] | None = None,
    defaults: Mapping[tuple[str, str], str] = _DEFAULTS,
) -> None:
    """Run the rule over *items* against the given stored values."""
    get_current, get_default = _readers(stored, defaults)

    async def _is_configured(namespace: str, key: str) -> bool:
        # Only the money-ceiling rule reads this, and nothing here writes
        # either of its keys, so it is never consulted on this path.
        return (namespace, key) in (stored or {})

    await enforce_cross_field_rules(
        items,
        get_current=get_current,
        get_default=get_default,
        is_configured=_is_configured,
    )


class TestScope:
    async def test_a_write_touching_no_rate_limit_key_reads_nothing(self) -> None:
        # Every settings write passes through here, so a rule that resolved
        # values on writes it does not judge would put four reads on the path
        # of every unrelated one.
        reads: list[tuple[str, str]] = []

        async def _get_current(namespace: str, key: str) -> str | None:
            reads.append((namespace, key))
            return None

        def _get_default(namespace: str, key: str) -> str | None:
            reads.append((namespace, key))
            return None

        async def _is_configured(namespace: str, key: str) -> bool:
            reads.append((namespace, key))
            return False

        await enforce_cross_field_rules(
            [("api", "server_port", "3001")],
            get_current=_get_current,
            get_default=_get_default,
            is_configured=_is_configured,
        )

        assert reads == []

    async def test_a_window_only_write_reads_nothing(self) -> None:
        # The window changes what every cap counts over without changing any
        # of them, and the caps it moves are all judged against each other in
        # the same unit. A rule reaching for it here judged the shipped
        # defaults and refused a write that altered no budget at all.
        reads: list[tuple[str, str]] = []

        async def _get_current(namespace: str, key: str) -> str | None:
            reads.append((namespace, key))
            return None

        def _get_default(namespace: str, key: str) -> str | None:
            reads.append((namespace, key))
            return None

        async def _is_configured(namespace: str, key: str) -> bool:
            reads.append((namespace, key))
            return False

        await enforce_cross_field_rules(
            [("api", "rate_limit_time_unit", "hour")],
            get_current=_get_current,
            get_default=_get_default,
            is_configured=_is_configured,
        )

        assert reads == []


class TestFloorCoversEveryTier:
    async def test_a_tier_above_the_floor_is_refused(self) -> None:
        with pytest.raises(SettingValidationError, match="rate_limit_auth_max"):
            await _enforce([("api", "rate_limit_auth_max_requests", "5000")])

    async def test_a_tier_equal_to_the_floor_is_allowed(self) -> None:
        await _enforce([("api", "rate_limit_auth_max_requests", "1000")])

    async def test_the_credential_throttle_is_exempt(self) -> None:
        # It counts over a fixed minute while the floor follows the general
        # window, so the two numbers are not comparable. It is also a ceiling
        # on attacker attempts rather than a budget anyone needs to reach: an
        # outer tier clipping it lower only tightens the bound.
        await _enforce([("api", "rate_limit_auth_endpoint_max_requests", "5000")])

    async def test_lowering_the_floor_under_a_stored_tier_is_refused(self) -> None:
        # The write names the floor, not the tier: the invariant is about the
        # pair, so it has to be resolved from what each key will hold.
        with pytest.raises(SettingValidationError, match="rate_limit_auth_max"):
            await _enforce(
                [("api", "rate_limit_floor_max_requests", "100")],
                stored={("api", "rate_limit_auth_max_requests"): "600"},
            )

    async def test_raising_both_in_one_write_is_allowed(self) -> None:
        # A batch is judged as one, so a pair that is only valid together can
        # be written together rather than needing an invalid intermediate.
        await _enforce(
            [
                ("api", "rate_limit_floor_max_requests", "5000"),
                ("api", "rate_limit_auth_max_requests", "5000"),
            ]
        )

    async def test_a_stored_value_beats_the_registered_default(self) -> None:
        await _enforce(
            [("api", "rate_limit_auth_max_requests", "900")],
            stored={("api", "rate_limit_floor_max_requests"): "1000"},
            defaults={("api", "rate_limit_floor_max_requests"): "100"},
        )

    async def test_an_unreadable_floor_judges_nothing(self) -> None:
        # Nothing resolves the floor, so there is no number to check against.
        # The per-field validator owns the malformed value; failing here as
        # well would report it twice and block a write for the wrong reason.
        await _enforce(
            [("api", "rate_limit_auth_max_requests", "5000")],
            defaults={},
        )

    async def test_a_malformed_tier_is_left_to_the_type_validator(self) -> None:
        await _enforce([("api", "rate_limit_auth_max_requests", "lots")])


_LADDER_DEFAULTS = {
    ("engine", "capability_floor_low"): "basic",
    ("engine", "capability_floor_normal"): "capable",
    ("engine", "capability_floor_high"): "expert",
    ("engine", "capability_floor_critical"): "expert",
    ("engine", "reasoning_effort_low"): "low",
    ("engine", "reasoning_effort_normal"): "low",
    ("engine", "reasoning_effort_high"): "medium",
    ("engine", "reasoning_effort_critical"): "high",
}


class TestEngineLadders:
    """Each ladder is one scale spread over four keys, written one at a time.

    Every rung is a legal value alone, so a single write can invert the ladder
    while passing per-field validation. The policy refuses an inverted ladder
    when it reads it, which is after the write has been acknowledged: from
    then on the dashboard shows a ladder the loop is not enforcing, and the
    next boot cannot build the policy at all.
    """

    async def _ladder(self, items: list[tuple[str, str, str]], **stored: str) -> None:
        await _enforce(
            items,
            stored={("engine", key): value for key, value in stored.items()},
            defaults=_LADDER_DEFAULTS,
        )

    async def test_a_floor_above_the_next_stakes_up_is_refused(self) -> None:
        with pytest.raises(SettingValidationError, match="capability floor"):
            await self._ladder([("engine", "capability_floor_low", "expert")])

    async def test_a_floor_below_the_stakes_beneath_it_is_refused(self) -> None:
        with pytest.raises(SettingValidationError, match="capability floor"):
            await self._ladder([("engine", "capability_floor_high", "basic")])

    async def test_an_effort_above_the_next_stakes_up_is_refused(self) -> None:
        with pytest.raises(SettingValidationError, match="reasoning effort"):
            await self._ladder([("engine", "reasoning_effort_normal", "high")])

    async def test_unset_ranks_below_every_effort(self) -> None:
        with pytest.raises(SettingValidationError, match="reasoning effort"):
            await self._ladder([("engine", "reasoning_effort_high", "none")])

    async def test_a_ladder_that_still_rises_is_accepted(self) -> None:
        await self._ladder([("engine", "capability_floor_normal", "expert")])

    async def test_both_ends_may_move_in_one_write(self) -> None:
        # The batch is judged as one, so a pair only valid together can be
        # written together rather than forcing an invalid intermediate state.
        await self._ladder(
            [
                ("engine", "capability_floor_low", "expert"),
                ("engine", "capability_floor_normal", "expert"),
            ]
        )

    async def test_a_flat_ladder_is_accepted(self) -> None:
        # Non-decreasing, not strictly increasing: an org that wants the same
        # rung everywhere is expressing a policy, not an inversion.
        await self._ladder(
            [
                ("engine", "capability_floor_high", "capable"),
                ("engine", "capability_floor_critical", "capable"),
            ],
            capability_floor_normal="capable",
        )

    async def test_a_malformed_rung_is_left_to_the_type_validator(self) -> None:
        await self._ladder([("engine", "capability_floor_high", "godlike")])

    async def test_a_write_touching_neither_ladder_reads_nothing(self) -> None:
        await self._ladder([("engine", "completion_oracle_enabled", "false")])


_STAGNATION_DEFAULTS = {
    ("engine", "stagnation_window_size"): "5",
    ("engine", "stagnation_min_tool_turns"): "2",
}


class TestStagnationFloorFitsWindow:
    """The repetition detector's floor has to fit inside its window.

    ``StagnationConfig`` refuses the pair when it is built, which happens at
    coordinator assembly and on every runtime rebuild a settings write
    triggers, so a pair accepted at the write is one the loop never runs
    under and one that fails every later engine write's rebuild.
    """

    async def _stagnation(
        self, items: list[tuple[str, str, str]], **stored: str
    ) -> None:
        await _enforce(
            items,
            stored={("engine", key): value for key, value in stored.items()},
            defaults=_STAGNATION_DEFAULTS,
        )

    async def test_a_floor_above_the_window_is_refused(self) -> None:
        with pytest.raises(SettingValidationError, match="exceeds"):
            await self._stagnation([("engine", "stagnation_min_tool_turns", "50")])

    async def test_narrowing_the_window_under_a_stored_floor_is_refused(
        self,
    ) -> None:
        with pytest.raises(SettingValidationError, match="exceeds"):
            await self._stagnation(
                [("engine", "stagnation_window_size", "3")],
                stagnation_min_tool_turns="4",
            )

    async def test_a_floor_equal_to_the_window_is_allowed(self) -> None:
        await self._stagnation([("engine", "stagnation_min_tool_turns", "5")])

    async def test_both_may_move_in_one_write(self) -> None:
        await self._stagnation(
            [
                ("engine", "stagnation_window_size", "40"),
                ("engine", "stagnation_min_tool_turns", "30"),
            ]
        )

    async def test_a_malformed_value_is_left_to_the_type_validator(self) -> None:
        await self._stagnation([("engine", "stagnation_min_tool_turns", "many")])

    async def test_a_write_touching_neither_key_reads_nothing(self) -> None:
        await self._stagnation([("engine", "stagnation_strategy", "off")])
