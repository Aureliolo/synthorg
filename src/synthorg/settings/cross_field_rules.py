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

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final, NoReturn

from synthorg.config.provider_schema import unwrap_provider_configs_envelope
from synthorg.core.billing_enums import MEASURABLE_BILLING_MODELS, BillingModel
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_FETCH_FAILED,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.settings.errors import SettingValidationError

logger = get_logger(__name__)

_API_NS: Final[str] = "api"
_FLOOR_KEY: Final[str] = "rate_limit_floor_max_requests"
# The floor middleware is applied app-wide, so it wraps every inner tier and a
# per-request budget above it can never be reached: the floor rejects first.
# These tiers share the floor's window, so their counts compare directly. The
# registered default stands in for an unset key so a first write is checked
# against what is actually in force rather than against nothing.
#
# ``rate_limit_auth_endpoint_max_requests`` is deliberately absent. It counts
# over a fixed minute while these follow ``rate_limit_time_unit``, so the
# numbers are not comparable, and it is a ceiling on attacker attempts rather
# than a budget anyone needs to reach: an outer tier clipping it lower only
# tightens the bound. Judging it here refuses valid writes, including one that
# only moves the window while every cap keeps its shipped default.
_TIER_KEYS: Final[tuple[str, ...]] = (
    "rate_limit_unauth_max_requests",
    "rate_limit_auth_max_requests",
)
# Membership only. Kept separate from the defaults, which are read from the
# registry: a copy here would enforce yesterday's number the moment someone
# retunes the registered one.
_RATE_LIMIT_KEYS: Final[frozenset[str]] = frozenset({_FLOOR_KEY, *_TIER_KEYS})

_BUDGET_NS: Final[str] = "budget"
_PROVIDERS_NS: Final[str] = "providers"
_CONFIGS_KEY: Final[str] = "configs"
_MONEY_CEILING_KEY: Final[str] = "run_hard_ceiling"
_TOKEN_CEILING_KEY: Final[str] = "run_hard_token_ceiling"  # noqa: S105 -- setting key, not a secret


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
    if any(ns == _API_NS and key in _RATE_LIMIT_KEYS for ns, key in written):
        await _enforce_rate_limit_floor(written, get_current, get_default)
    # Either side can break the pair. Watching only the ceiling leaves the
    # other direction unguarded: with a ceiling already stored, a write to
    # the provider set that drops the last metered connection produces the
    # same unbindable state the rule exists to refuse.
    if (_BUDGET_NS, _MONEY_CEILING_KEY) in written or (
        _PROVIDERS_NS,
        _CONFIGS_KEY,
    ) in written:
        await _enforce_money_ceiling_can_bind(written, get_current)


async def _enforce_money_ceiling_can_bind(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
) -> None:
    """Refuse a money ceiling no configured connection could ever cross.

    A provider that bills by flat subscription records a cost of 0.0 on every
    call, which is the correct number and never rises. A money ceiling set
    against an estate made entirely of those measures nothing: the operator
    reads a bound the run does not have, which is the whole failure. It is
    refused rather than warned about, so an unbindable knob cannot be left
    configured and believed.

    Only fires when at least one connection is configured. With none there is
    no evidence either way, and an operator setting policy before adding a
    provider is doing it in the sensible order.

    The ceiling is resolved from the batch, then from what is stored, and
    deliberately NOT from the registered default: unlike the rate-limit floor,
    which judges what is in force, this judges what the operator asked for. A
    flat-rate estate whose ceiling is only the shipped default has expressed
    no intent, and refusing there would make its very first connection
    unaddable over a number nobody chose.

    Raises:
        SettingValidationError: When every configured connection bills by
            something a money ceiling cannot measure.
    """
    raw_ceiling = written.get((_BUDGET_NS, _MONEY_CEILING_KEY))
    if raw_ceiling is None:
        raw_ceiling = await get_current(_BUDGET_NS, _MONEY_CEILING_KEY)
    if raw_ceiling is None:
        return
    try:
        ceiling = float(raw_ceiling)
    except ValueError:
        return
    if ceiling <= 0:
        # 0 is the documented opt-out, and switching enforcement OFF is
        # always allowed however the estate bills.
        return
    # The batch's own provider set wins over the stored one: adding a metered
    # connection and setting the ceiling is a single coherent write, and
    # judging it against the pre-write estate refuses it for a state the
    # operator is in the act of leaving.
    raw = written.get((_PROVIDERS_NS, _CONFIGS_KEY)) or await get_current(
        _PROVIDERS_NS, _CONFIGS_KEY
    )
    billing = _configured_billing_models(raw)
    if not billing or any(model in MEASURABLE_BILLING_MODELS for model in billing):
        return
    flat = BillingModel.FLAT_RATE.value
    msg = (
        f"{_BUDGET_NS}.{_MONEY_CEILING_KEY} of {ceiling} cannot bind: every"
        f" configured provider connection bills by something a per-token cost"
        f" cannot measure, so the accumulated cost it compares against stays"
        f" at zero for the life of every run. Set"
        f" {_BUDGET_NS}.{_TOKEN_CEILING_KEY} instead, which is counted on"
        f" every provider, or declare a per-token connection. A connection's"
        f" billing model is its own field; correct it there if one of these"
        f" is not really {flat}."
    )
    _reject(
        _MONEY_CEILING_KEY,
        msg,
        reason="money ceiling against an unmeasurable estate",
        namespace=_BUDGET_NS,
    )


def _configured_billing_models(raw: str | None) -> tuple[BillingModel, ...]:
    """Return the billing model of every configured provider connection.

    Args:
        raw: The ``providers.configs`` envelope this write will leave in
            place, or ``None`` when none is configured.

    Returns:
        One entry per configured connection, empty when none are configured
        or the envelope cannot be read. An unreadable envelope yields nothing
        rather than a guess, so this rule refuses nothing it cannot see.
    """
    if raw is None:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError:
        # Fail open, but say so: this rule declines to judge an envelope it
        # cannot read, and silence would leave the operator with a ceiling
        # accepted for a reason nothing recorded.
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=_PROVIDERS_NS,
            key=_CONFIGS_KEY,
            reason="invalid_json_billing_check_skipped",
        )
        return ()
    configs = unwrap_provider_configs_envelope(parsed, {})
    return tuple(config.billing_model for config in configs.values())


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
        _reject(tier_key, msg, reason="tier budget above the IP floor")


def _reject(key: str, msg: str, *, reason: str, namespace: str = _API_NS) -> NoReturn:
    """Log the refusal with operator context, then raise it.

    Args:
        key: The setting whose value made the combination invalid.
        msg: The operator-facing explanation.
        reason: The invariant the write broke, for the structured log.
        namespace: The setting's namespace, for the structured log.

    Raises:
        SettingValidationError: Always, carrying ``msg``.
    """
    logger.warning(
        SETTINGS_VALIDATION_FAILED,
        namespace=namespace,
        key=key,
        reason=reason,
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
