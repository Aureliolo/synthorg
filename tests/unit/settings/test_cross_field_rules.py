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
    await enforce_cross_field_rules(
        items, get_current=get_current, get_default=get_default
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

        await enforce_cross_field_rules(
            [("api", "server_port", "3001")],
            get_current=_get_current,
            get_default=_get_default,
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

        await enforce_cross_field_rules(
            [("api", "rate_limit_time_unit", "hour")],
            get_current=_get_current,
            get_default=_get_default,
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
