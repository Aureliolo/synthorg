"""Resolve the operator-configured display currency with a safe fallback.

Eight call sites previously inlined ``config_resolver.get_str("budget",
"currency")`` plus bespoke ``reraise_critical`` + WARNING + ``DEFAULT_CURRENCY``
fallback boilerplate. This module centralises that read so the fallback policy
lives in one place: each site collapses to
``resolve_currency(config_resolver_of(app_state))`` (or, where a resolver is
held directly, ``resolve_currency(self._config_resolver)``).

Kept separate from :mod:`synthorg.budget.currency` (which is imported by every
cost-bearing model for the ``CurrencyCode`` type) so the ``settings``-layer
import stays out of that widely-imported leaf and cannot seed an import cycle.
"""

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import BUDGET_CURRENCY_RESOLVE_FAILED
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


async def resolve_currency(resolver: ConfigResolver | None) -> str:
    """Resolve ``budget.currency``, falling back to ``DEFAULT_CURRENCY``.

    A ``None`` resolver (service not wired) or any non-critical failure
    yields ``DEFAULT_CURRENCY`` after a WARNING; critical errors
    (``MemoryError`` / ``RecursionError``) re-raise.

    Args:
        resolver: The settings resolver, or ``None`` when unavailable.

    Returns:
        The configured ISO 4217 currency code, or ``DEFAULT_CURRENCY``.
    """
    if resolver is None:
        return DEFAULT_CURRENCY
    try:
        return await resolver.get_str("budget", "currency")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
        reraise_critical(exc)
        logger.warning(
            BUDGET_CURRENCY_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DEFAULT_CURRENCY


__all__ = ["resolve_currency"]
