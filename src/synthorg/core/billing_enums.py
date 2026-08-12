# module-kind: code
"""How a provider charges for what it serves.

A spend ceiling is denominated in money, and money is only a measure of usage
where the provider bills per token. Against a flat-rate subscription every call
costs nothing, which is the correct record and not a defect: there is no
per-1k price to attribute. The defect is a budget surface that reads a
permanent zero as permanent headroom, so an operator cannot tell "we have spent
nothing" from "this ceiling cannot measure what we are spending".

The declaration lives here, in ``core``, because the preset that seeds it
(``providers``), the config that owns it (``config``), and the ledger that
stamps it onto every row (``budget``) all need the same vocabulary, and
``core`` is the one package all three may import.
"""

from collections.abc import Iterable
from enum import StrEnum


class BillingModel(StrEnum):
    """How a provider connection charges for the calls it serves.

    ``PER_TOKEN`` is the case a money ceiling can bind: cost is a function of
    the tokens sent and returned. ``FLAT_RATE`` is a subscription, where usage
    is real and its price is not per call, so a money total is always zero and
    a money ceiling binds nothing. ``UNKNOWN`` is the honest answer for a
    connection nobody has declared, and it is treated as unmeasurable rather
    than as per-token: assuming a ceiling binds when it may not is the failure
    being fixed, and assuming it does not costs only a prompt to declare.
    """

    PER_TOKEN = "per_token"  # noqa: S105 -- billing concept, not a secret
    FLAT_RATE = "flat_rate"
    UNKNOWN = "unknown"


#: Billing models whose spend a money-denominated ceiling can actually measure.
#: Stated as an allowlist rather than as "not FLAT_RATE", so a future billing
#: model is unmeasurable until somebody says otherwise.
MEASURABLE_BILLING_MODELS: frozenset[BillingModel] = frozenset({BillingModel.PER_TOKEN})


def money_ceiling_can_bind(billing_models: Iterable[BillingModel]) -> bool:
    """Answer whether a money ceiling could ever fire on this estate.

    The one owner of that question. Two write paths ask it, the global
    ``budget.run_hard_ceiling`` setting and a task's own ``hard_ceiling``,
    and they reach it with different evidence in hand: the settings rule has
    the ``providers.configs`` envelope it is about to persist, the task guard
    has the live registry. Two copies of the predicate would be two answers
    the moment either one changes, and the quieter would decide whichever
    write happened to go through it.

    Args:
        billing_models: One entry per configured connection, in any order.

    Returns:
        ``True`` when at least one connection bills by something a per-token
        cost can measure. An empty estate also answers ``True``: with no
        connection there is no evidence either way, and refusing there would
        make an operator's first connection unaddable over a bound they set
        in the sensible order.
    """
    models = tuple(billing_models)
    return not models or any(model in MEASURABLE_BILLING_MODELS for model in models)
