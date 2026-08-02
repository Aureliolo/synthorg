"""Rules a single setting's value cannot express on its own.

Per-field validation runs from the definition alone, so an invariant that
spans two settings has nowhere to live there. Checking it only where the
value is consumed is too late: the write has already been committed and
acknowledged, the dashboard reports a value the system is not enforcing, and
every later write to a key in the same group re-fails the same rebuild, so a
genuine tightening during an incident can silently fail too.

These run at write time, before anything is persisted, so an invalid
combination is refused with an error the caller actually sees.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final, NoReturn, cast

from synthorg.config.rate_limits import (
    AUTH_ENDPOINT_WINDOW,
    KNOWN_WINDOWS,
    RateLimitWindowUnit,
    exceeds_window_rate,
)
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_VALIDATION_FAILED
from synthorg.settings.errors import SettingValidationError

logger = get_logger(__name__)

_API_NS: Final[str] = "api"
_FLOOR_KEY: Final[str] = "rate_limit_floor_max_requests"
_WINDOW_KEY: Final[str] = "rate_limit_time_unit"
# The floor middleware is applied app-wide, so it wraps every inner tier and a
# per-request budget above it can never be reached: the floor rejects first.
# These tiers share the floor's window, so their counts compare directly. The
# registered default stands in for an unset key so a first write is checked
# against what is actually in force rather than against nothing.
_TIER_KEYS: Final[tuple[str, ...]] = (
    "rate_limit_unauth_max_requests",
    "rate_limit_auth_max_requests",
)
# Route middleware on /auth/*, which the floor still sits in front of, so the
# same invariant applies. Its window is pinned to a minute whatever the general
# one is set to, so it is compared as a rate rather than as a raw count.
_AUTH_ENDPOINT_KEY: Final[str] = "rate_limit_auth_endpoint_max_requests"
# Membership only. Kept separate from the defaults, which are read from the
# registry: a copy here would enforce yesterday's number the moment someone
# retunes the registered one.
_RATE_LIMIT_KEYS: Final[frozenset[str]] = frozenset(
    {_FLOOR_KEY, _WINDOW_KEY, _AUTH_ENDPOINT_KEY, *_TIER_KEYS}
)


async def enforce_cross_field_rules(
    items: Sequence[tuple[str, str, str]],
    *,
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
) -> None:
    """Reject a write whose combined result breaks a cross-setting invariant.

    Args:
        items: The ``(namespace, key, value)`` triples about to be written.
            A batch is checked as one, so a pair that is only valid together
            can be written together.
        get_current: Resolves the stored value, ``None`` when unset.
        get_default: Resolves the registered default for an unset key.

    Raises:
        SettingValidationError: When the resulting combination is invalid.
    """
    written = {(namespace, key): value for namespace, key, value in items}
    if not any(ns == _API_NS and key in _RATE_LIMIT_KEYS for ns, key in written):
        return
    await _enforce_rate_limit_floor(written, get_current, get_default)


async def _enforce_rate_limit_floor(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
) -> None:
    """Reject a rate-limit write leaving a tier budget above the IP floor.

    Raises:
        SettingValidationError: When a tier cap would exceed the floor.
    """
    floor = await _effective(written, get_current, get_default, _FLOOR_KEY)
    if floor is None:
        return
    for tier_key in _TIER_KEYS:
        cap = await _effective(written, get_current, get_default, tier_key)
        if cap is None or cap <= floor:
            continue
        msg = (
            f"api.{tier_key} of {cap} exceeds api.{_FLOOR_KEY} of {floor}; the"
            " floor wraps every tier, so the larger budget could never be"
            " reached. Raise the floor in the same write, or lower the tier."
        )
        _reject(tier_key, msg)
    await _enforce_credential_floor(written, get_current, get_default, floor)


async def _enforce_credential_floor(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
    floor: int,
) -> None:
    """Reject a credential cap asking for a higher rate than the floor allows.

    The credential throttle counts over a fixed minute whatever the general
    window is set to, so the two budgets are only comparable as rates: a
    floor of 10 per second leaves room for far more than 10 logins a minute.

    Args:
        written: The values this batch is about to write.
        get_current: Resolves the stored value, ``None`` when unset.
        get_default: Resolves the registered default for an unset key.
        floor: The app-wide ceiling this write settles on.

    Raises:
        SettingValidationError: When the credential cap is unreachable.
    """
    cap = await _effective(written, get_current, get_default, _AUTH_ENDPOINT_KEY)
    if cap is None:
        return
    raw_window = written.get((_API_NS, _WINDOW_KEY)) or await get_current(
        _API_NS, _WINDOW_KEY
    )
    raw_window = raw_window or get_default(_API_NS, _WINDOW_KEY)
    if raw_window not in KNOWN_WINDOWS:
        return
    window = cast("RateLimitWindowUnit", raw_window)
    if not exceeds_window_rate(
        cap=cap,
        cap_window=AUTH_ENDPOINT_WINDOW,
        floor=floor,
        floor_window=window,
    ):
        return
    msg = (
        f"api.{_AUTH_ENDPOINT_KEY} of {cap} per {AUTH_ENDPOINT_WINDOW} is a"
        f" higher rate than api.{_FLOOR_KEY} of {floor} per {window}; the floor"
        " wraps the credential endpoints, so that budget could never be"
        " reached. Raise the floor in the same write, or lower the tier."
    )
    _reject(_AUTH_ENDPOINT_KEY, msg)


def _reject(key: str, msg: str) -> NoReturn:
    """Log the refusal with operator context, then raise it.

    Args:
        key: The setting whose value made the combination invalid.
        msg: The operator-facing explanation.

    Raises:
        SettingValidationError: Always, carrying ``msg``.
    """
    logger.warning(
        SETTINGS_VALIDATION_FAILED,
        namespace=_API_NS,
        key=key,
        reason="tier budget above the IP floor",
    )
    raise SettingValidationError(msg)


async def _effective(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
    key: str,
) -> int | None:
    """Return the value this key will hold after the write.

    A read that fails propagates rather than standing in the registered
    default: this rule decides whether to accept a write, and checking a
    weakening against a number that is not in force would approve exactly
    the combination it exists to refuse.

    Returns:
        The resolved integer, or ``None`` when it does not parse. A malformed
        value is rejected by the per-field type validator, so it is not this
        rule's job to report it a second time.
    """
    raw = written.get((_API_NS, key))
    if raw is None:
        raw = await get_current(_API_NS, key)
    if raw is None:
        raw = get_default(_API_NS, key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
