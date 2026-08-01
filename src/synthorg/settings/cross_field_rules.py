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
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_VALIDATION_FAILED
from synthorg.settings.errors import SettingValidationError

logger = get_logger(__name__)

_API_NS: Final[str] = "api"
_FLOOR_KEY: Final[str] = "rate_limit_floor_max_requests"
# The floor middleware wraps both inner tiers, so a per-request budget above
# the floor can never be reached: the floor rejects first. Registered defaults
# stand in for an unset key so a first write is checked against what is
# actually in force rather than against nothing.
_TIER_KEYS: Final[tuple[str, ...]] = (
    "rate_limit_unauth_max_requests",
    "rate_limit_auth_max_requests",
)
_RATE_LIMIT_DEFAULTS: Final[Mapping[str, str]] = {
    _FLOOR_KEY: "10000",
    "rate_limit_unauth_max_requests": "20",
    "rate_limit_auth_max_requests": "6000",
}


async def enforce_cross_field_rules(
    items: Sequence[tuple[str, str, str]],
    *,
    get_current: Callable[[str, str], Awaitable[str | None]],
) -> None:
    """Reject a write whose combined result breaks a cross-setting invariant.

    Args:
        items: The ``(namespace, key, value)`` triples about to be written.
            A batch is checked as one, so a pair that is only valid together
            can be written together.
        get_current: Resolves the stored value, ``None`` when unset.

    Raises:
        SettingValidationError: When the resulting combination is invalid.
    """
    written = {(namespace, key): value for namespace, key, value in items}
    if not any(ns == _API_NS and key in _RATE_LIMIT_DEFAULTS for ns, key in written):
        return
    await _enforce_rate_limit_floor(written, get_current)


async def _enforce_rate_limit_floor(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
) -> None:
    """Reject a rate-limit write leaving a tier budget above the IP floor.

    Raises:
        SettingValidationError: When a tier cap would exceed the floor.
    """
    floor = await _effective(written, get_current, _FLOOR_KEY)
    if floor is None:
        return
    for tier_key in _TIER_KEYS:
        cap = await _effective(written, get_current, tier_key)
        if cap is None or cap <= floor:
            continue
        msg = (
            f"api.{tier_key} of {cap} exceeds api.{_FLOOR_KEY} of {floor}; the"
            " floor wraps both tiers, so the larger budget could never be"
            " reached. Raise the floor in the same write, or lower the tier."
        )
        logger.warning(
            SETTINGS_VALIDATION_FAILED,
            namespace=_API_NS,
            key=tier_key,
            reason="tier budget above the IP floor",
        )
        raise SettingValidationError(msg)


async def _effective(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    key: str,
) -> int | None:
    """Return the value this key will hold after the write.

    Returns:
        The resolved integer, or ``None`` when it does not parse. A malformed
        value is rejected by the per-field type validator, so it is not this
        rule's job to report it a second time.
    """
    raw = written.get((_API_NS, key))
    if raw is None:
        try:
            raw = await get_current(_API_NS, key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            raw = None
    if raw is None:
        raw = _RATE_LIMIT_DEFAULTS[key]
    try:
        return int(raw)
    except ValueError:
        return None
