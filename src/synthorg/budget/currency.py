"""Currency display formatting utilities and the ``CurrencyCode`` type.

Provides locale-independent currency formatting using ISO 4217 codes.
No external dependencies -- symbol lookup uses a built-in table of
common currencies with fallback to the ISO code for unknown codes.

Owns two related concerns:

* **Display formatting** -- ``format_cost`` / ``format_cost_detail`` /
  ``get_currency_symbol`` / ``CURRENCY_SYMBOLS`` / ``MINOR_UNITS``.
* **Validation** -- the ``CurrencyCode`` Annotated type used on every
  cost-bearing model (``CostRecord``, ``TaskMetricRecord``,
  ``AgentRuntimeState``, et al.) to reject typos and codes the display
  layer does not know how to format.

The ``CurrencyCode`` allowlist is derived from the union of keys in
``CURRENCY_SYMBOLS`` and ``MINOR_UNITS`` so that adopting a new
currency requires adding it to the display mappings first; a row with
a code the formatter cannot render would be a latent bug waiting to
surface in a report.
"""

import math
from collections.abc import Iterable  # noqa: TC003 -- runtime type
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import AfterValidator, StringConstraints

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.budget import BUDGET_MIXED_CURRENCY_REJECTED

logger = get_logger(__name__)

DEFAULT_CURRENCY: Final[str] = "USD"
"""Default ISO 4217 currency code.

USD is the honest default: major LLM providers publish token pricing
in USD, and LiteLLM returns ``response_cost`` in USD.  SynthOrg does
not convert FX at record time or display time -- the
``budget.currency`` setting is a display-only preference.  Overridden
at runtime by the ``budget.currency`` setting; this constant is the
fallback when the setting has not been resolved yet.
"""

CURRENCY_SYMBOLS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "AUD": "A$",
        "BRL": "R$",
        "CAD": "CA$",
        "CHF": "CHF",
        "CNY": "CN\u00a5",
        "CZK": "K\u010d",
        "DKK": "kr",
        "EUR": "\u20ac",
        "GBP": "\u00a3",
        "HKD": "HK$",
        "HUF": "Ft",
        "IDR": "Rp",
        "ILS": "\u20aa",
        "INR": "\u20b9",
        "JPY": "\u00a5",
        "KRW": "\u20a9",
        "MXN": "MX$",
        "NOK": "kr",
        "NZD": "NZ$",
        "PLN": "z\u0142",
        "SEK": "kr",
        "SGD": "S$",
        "THB": "\u0e3f",
        "TRY": "\u20ba",
        "TWD": "NT$",
        "USD": "$",
        "VND": "\u20ab",
        "ZAR": "R",
    }
)
"""Mapping of common ISO 4217 currency codes to display symbols."""

MINOR_UNITS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        # Zero-decimal currencies (ISO 4217 exponent 0)
        "BIF": 0,
        "CLP": 0,
        "DJF": 0,
        "GNF": 0,
        "HUF": 0,  # ISO exponent=2 but integer for display (HNB)
        "ISK": 0,
        "JPY": 0,
        "KMF": 0,
        "KRW": 0,
        "MGA": 0,
        "PYG": 0,
        "RWF": 0,
        "UGX": 0,
        "VND": 0,
        "VUV": 0,
        "XAF": 0,
        "XOF": 0,
        "XPF": 0,
        # Three-decimal currencies (ISO 4217 exponent 3)
        "BHD": 3,
        "IQD": 3,
        "JOD": 3,
        "KWD": 3,
        "LYD": 3,
        "OMR": 3,
        "TND": 3,
    }
)
"""ISO 4217 minor-unit metadata.

Maps currency codes to their number of minor (fractional) units.
Currencies not listed default to 2 decimal places (the ISO 4217 norm).
"""


def get_currency_symbol(code: str) -> str:
    """Return the display symbol for an ISO 4217 currency code.

    Falls back to the code itself (e.g. ``"AED"``) when no dedicated
    symbol is mapped.

    Args:
        code: ISO 4217 currency code (e.g. ``"USD"``, ``"EUR"``).

    Returns:
        The currency symbol string.
    """
    return CURRENCY_SYMBOLS.get(code, code)


def format_cost(
    value: float,
    currency: str = DEFAULT_CURRENCY,
    *,
    precision: int | None = None,
) -> str:
    """Format a cost value with the appropriate currency symbol.

    Uses the symbol from ``CURRENCY_SYMBOLS`` (or the ISO code as
    fallback) and the appropriate number of decimal places for the
    currency based on ``MINOR_UNITS``.

    Args:
        value: The numeric cost value (must be finite).
        currency: ISO 4217 currency code.
        precision: Override decimal places.  When ``None``, uses the
            currency's minor-unit count from ``MINOR_UNITS`` (default 2).

    Returns:
        Formatted string, e.g. ``"$42.50"``, ``"\u20ac10.00"``,
        ``"\u00a51,234"``.

    Raises:
        ValueError: If *value* is not finite or *precision* is negative.
    """
    if not math.isfinite(value):
        msg = f"Cannot format non-finite cost value: {value!r}"
        raise ValueError(msg)
    if precision is not None and precision < 0:
        msg = f"precision must be non-negative, got {precision}"
        raise ValueError(msg)
    if precision is None:
        precision = MINOR_UNITS.get(currency, 2)
    symbol = get_currency_symbol(currency)
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.{precision}f}"


def format_cost_detail(value: float, currency: str = DEFAULT_CURRENCY) -> str:
    """Format a cost value with 4-decimal precision for detail views.

    Used in activity feeds and line-item displays where sub-unit
    precision matters (e.g. individual API call costs).

    Args:
        value: The numeric cost value.
        currency: ISO 4217 currency code.

    Returns:
        Formatted string with 4 decimal places, e.g. ``"$0.0315"``.
    """
    return format_cost(value, currency, precision=4)


_KNOWN_ISO4217: Final[frozenset[str]] = frozenset(CURRENCY_SYMBOLS) | frozenset(
    MINOR_UNITS
)
"""Allowlist of ISO 4217 codes the display/formatting layer supports.

Drawn from ``CURRENCY_SYMBOLS`` and ``MINOR_UNITS``.  A new currency
must be added to at least one of those mappings before any row may
carry the code; this keeps persistence and formatting in sync.
"""


def _check_iso4217(value: str) -> str:
    """Reject currency codes not present in the known ISO 4217 allowlist.

    Returns:
        Result of type ``str``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if value not in _KNOWN_ISO4217:
        msg = f"unknown ISO 4217 currency code: {value!r}"
        raise ValueError(msg)
    return value


_MISSING_CURRENCY: Final[str] = "<missing>"
"""Sentinel surfaced in mixed-currency errors when a row has no code.

The mixed-currency guard accepts ``None`` codes (e.g. an aggregation
where one row lost its currency due to a partial write) and reports
them under this label so structured-log consumers and error envelopes
can distinguish "two real currencies were mixed" from "a row was
missing its currency entirely".
"""


CurrencyCode = Annotated[
    str,
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
    AfterValidator(_check_iso4217),
]
"""An ISO 4217 currency code (3 uppercase ASCII letters).

Validation is two-phase: the string pattern rejects blank, lowercase,
and wrong-length inputs at parse time; the ``AfterValidator`` then
requires the code to be present in ``_KNOWN_ISO4217`` so typos
(``EURR``) and well-formed but unsupported codes (``ZZZ``) are rejected
together.
"""


def assert_currencies_match(
    currencies: Iterable[str | None],
    *,
    agent_id: NotBlankStr | None = None,
    task_id: NotBlankStr | None = None,
    project_id: NotBlankStr | None = None,
    department_id: NotBlankStr | None = None,
) -> CurrencyCode | None:
    """Verify every currency code in *currencies* is identical.

    The same-currency invariant for cost aggregation: callers pass an
    iterable of ISO 4217 codes pulled from the items they are about to
    sum / mean / otherwise reduce.  Empty input returns ``None`` (no
    aggregation, no currency).  Mixed input logs at WARNING and raises
    :class:`~synthorg.budget.errors.MixedCurrencyAggregationError`
    (HTTP 409) **before** any reduction runs, so the caller cannot
    silently produce a meaningless total.  ``None`` codes are treated
    as a distinct value: an iterable mixing ``None`` with a real code
    raises just like any other mismatch, so callers cannot silently
    bypass the guard by passing optional fields without filtering.

    The contextual ``agent_id`` / ``task_id`` / ``project_id`` /
    ``department_id`` keyword arguments are propagated to both the
    warning log and the raised exception so structured-log consumers
    can trace the rejected aggregation back to its scope.  The four
    dimensions are deliberately distinct so a per-department rollup
    cannot accidentally surface a department name as if it were a
    project identifier.

    Args:
        currencies: Iterable of ISO 4217 codes (e.g. ``r.currency for r
            in records``).  Single-pass iterables (generators) are
            supported; the iterable is consumed exactly once.
        agent_id: Optional agent identifier the aggregation targeted.
        task_id: Optional task identifier the aggregation targeted.
        project_id: Optional project identifier the aggregation
            targeted.
        department_id: Optional department identifier the aggregation
            targeted (used by per-department rollups).

    Returns:
        The single shared currency code, or ``None`` for empty input.

    Raises:
        MixedCurrencyAggregationError: If two or more distinct codes
            are observed.
    """
    codes: set[str | None] = set(currencies)
    if not codes:
        return None
    if codes == {None}:
        # Non-empty iterable of only-missing codes: fail closed instead
        # of silently returning ``None`` and letting the caller reduce
        # rows in an undefined unit.
        normalized = frozenset({_MISSING_CURRENCY})
        logger.warning(
            BUDGET_MIXED_CURRENCY_REJECTED,
            currencies=sorted(normalized),
            agent_id=agent_id,
            task_id=task_id,
            project_id=project_id,
            department_id=department_id,
        )
        raise MixedCurrencyAggregationError(
            currencies=normalized,
            agent_id=agent_id,
            task_id=task_id,
            project_id=project_id,
            department_id=department_id,
        )
    if len(codes) > 1:
        normalized = frozenset(_MISSING_CURRENCY if c is None else c for c in codes)
        logger.warning(
            BUDGET_MIXED_CURRENCY_REJECTED,
            currencies=sorted(normalized),
            agent_id=agent_id,
            task_id=task_id,
            project_id=project_id,
            department_id=department_id,
        )
        raise MixedCurrencyAggregationError(
            currencies=normalized,
            agent_id=agent_id,
            task_id=task_id,
            project_id=project_id,
            department_id=department_id,
        )
    return next(iter(codes))
