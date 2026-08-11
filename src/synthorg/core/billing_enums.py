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
