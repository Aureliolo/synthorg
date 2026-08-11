"""A money ceiling that could never fire is refused at write time.

A provider that bills by flat subscription records a cost of 0.0 on every
call, correctly and forever. A money ceiling set against an estate made
entirely of those measures nothing: the operator reads a bound the run does
not have. Refused rather than warned about, so an unbindable knob cannot be
left configured and believed.
"""

import json
from collections.abc import Awaitable, Callable, Sequence

import pytest

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.settings.cross_field_rules import enforce_cross_field_rules
from synthorg.settings.errors import SettingValidationError

pytestmark = pytest.mark.unit

_CEILING = ("budget", "run_hard_ceiling", "25.0")


def _envelope(**models: BillingModel) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "providers": {
                name: ProviderConfig(
                    driver="scripted",
                    connection_name=f"conn-{name}",
                    billing_model=model,
                ).model_dump(mode="json")
                for name, model in models.items()
            },
        }
    )


def _readers(
    providers_blob: str | None,
) -> tuple[
    Callable[[str, str], Awaitable[str | None]],
    Callable[[str, str], str | None],
]:
    async def _current(namespace: str, key: str) -> str | None:
        if (namespace, key) == ("providers", "configs"):
            return providers_blob
        return None

    def _default(namespace: str, key: str) -> str | None:
        return None

    return _current, _default


async def _enforce(
    items: Sequence[tuple[str, str, str]],
    providers_blob: str | None,
) -> None:
    current, default = _readers(providers_blob)
    await enforce_cross_field_rules(items, get_current=current, get_default=default)


async def _accepts(
    items: Sequence[tuple[str, str, str]],
    providers_blob: str | None,
) -> None:
    """Assert the write is accepted, naming the estate when it is not.

    ``enforce_cross_field_rules`` returns nothing, so acceptance is only ever
    observable as the absence of the refusal; what this adds is a failure
    message that says which estate was judged.
    """
    try:
        await _enforce(items, providers_blob)
    except SettingValidationError as exc:
        pytest.fail(f"refused against estate {providers_blob!r}: {exc}")


async def test_an_all_flat_rate_estate_refuses_the_money_ceiling() -> None:
    blob = _envelope(gateway=BillingModel.FLAT_RATE)

    with pytest.raises(SettingValidationError, match="cannot bind"):
        await _enforce([_CEILING], blob)


async def test_an_unknown_estate_refuses_too() -> None:
    # UNKNOWN reads as unmeasurable rather than as per-token: assuming a
    # ceiling binds when it may not is the failure being fixed.
    blob = _envelope(mystery=BillingModel.UNKNOWN)

    with pytest.raises(SettingValidationError, match="cannot bind"):
        await _enforce([_CEILING], blob)


async def test_one_metered_connection_is_enough() -> None:
    blob = _envelope(
        gateway=BillingModel.FLAT_RATE,
        metered=BillingModel.PER_TOKEN,
    )

    await _accepts([_CEILING], blob)


async def test_an_empty_estate_is_not_evidence() -> None:
    # Setting policy before adding a connection is the sensible order, and
    # an estate with no connection in it says nothing either way.
    await _accepts([_CEILING], _envelope())


async def test_an_absent_estate_is_not_evidence() -> None:
    await _accepts([_CEILING], None)


async def test_an_unreadable_estate_fails_open_rather_than_guessing() -> None:
    # Refusing on an envelope this rule cannot read would block a ceiling for
    # a state nobody can see; judging it as measurable would be a guess. The
    # readable form of the same estate is refused, so the acceptance is caused
    # by the unreadability rather than by the rule never running.
    unreadable = _envelope(gateway=BillingModel.FLAT_RATE)[:-4]

    await _accepts([_CEILING], unreadable)

    with pytest.raises(SettingValidationError, match="cannot bind"):
        await _enforce([_CEILING], _envelope(gateway=BillingModel.FLAT_RATE))


async def test_a_non_numeric_ceiling_is_left_to_the_type_validator() -> None:
    # The per-field validator owns "that is not a number"; reporting it here
    # too would give one bad value two different refusals.
    blob = _envelope(gateway=BillingModel.FLAT_RATE)

    await _accepts([("budget", "run_hard_ceiling", "quite a lot")], blob)


async def test_switching_enforcement_off_is_always_allowed() -> None:
    blob = _envelope(gateway=BillingModel.FLAT_RATE)

    await _accepts([("budget", "run_hard_ceiling", "0")], blob)


async def test_the_token_ceiling_is_never_refused() -> None:
    blob = _envelope(gateway=BillingModel.FLAT_RATE)

    await _accepts([("budget", "run_hard_token_ceiling", "50000000")], blob)
