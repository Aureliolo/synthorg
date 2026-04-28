"""Bounded label value sets + helpers for the Prometheus collector.

Every free-form label on a metric is validated against a frozenset
here so a bad call site fails loudly at push time instead of
silently polluting cardinality. The sets and the :func:`_status_class`
helper live in their own module so :mod:`synthorg.observability.prometheus_collector`
stays below the 800-line limit mandated by ``CLAUDE.md``.
"""

import math
from typing import Final, get_args

from synthorg.core.error_taxonomy import ErrorCategory
from synthorg.observability import get_logger
from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.providers.errors import ProviderErrorLabel

__all__ = [
    "VALID_API_ERROR_CATEGORIES",
    "VALID_AUDIT_APPEND_STATUSES",
    "VALID_CACHE_NAMES",
    "VALID_CACHE_OUTCOMES",
    "VALID_IDENTITY_CHANGE_TYPES",
    "VALID_OTLP_KINDS",
    "VALID_OTLP_OUTCOMES",
    "VALID_PROVIDER_ERROR_CLASSES",
    "VALID_STATUS_CLASSES",
    "VALID_TASK_OUTCOMES",
    "VALID_TOKEN_DIRECTIONS",
    "VALID_TOOL_OUTCOMES",
    "VALID_VERDICTS",
    "VALID_WORKFLOW_EXECUTION_STATUSES",
    "require_finite",
    "require_label",
    "require_non_negative",
    "status_class",
]

logger = get_logger(__name__)


def require_label(label: str, value: str, valid: frozenset[str]) -> None:
    """Raise ``ValueError`` if *value* is not in the allowed set.

    Emits a ``WARNING`` log with the rejected value before raising
    so a misbehaving call site is visible in monitoring -- a bare
    ``ValueError`` at the raise site would be invisible unless
    every caller logged it themselves.
    """
    if value not in valid:
        logger.warning(
            METRICS_SCRAPE_FAILED,
            reason="invalid_label",
            label=label,
            rejected_value=value,
            allowed=sorted(valid),
        )
        msg = f"Unknown {label} {value!r}; expected one of {sorted(valid)}"
        raise ValueError(msg)


def require_finite(label: str, value: float | int) -> None:
    """Raise ``ValueError`` if *value* is NaN or infinite.

    Prometheus will happily store NaN/Inf, but dashboards that rely
    on rate() or quantile aggregations break silently when they
    arrive, so every numeric input goes through this guard before
    hitting ``Counter.inc()`` / ``Histogram.observe()``.
    """
    if not math.isfinite(value):
        logger.warning(
            METRICS_SCRAPE_FAILED,
            reason="non_finite_value",
            label=label,
            rejected_value=str(value),
        )
        msg = f"{label} must be a finite number, got {value!r}"
        raise ValueError(msg)


def require_non_negative(label: str, value: float | int) -> None:
    """Raise ``ValueError`` if *value* is negative, NaN, or infinite.

    Calls :func:`require_finite` first so NaN values (which compare
    ``!= 0`` in both directions) are caught before the sign test.
    """
    require_finite(label, value)
    if value < 0:
        logger.warning(
            METRICS_SCRAPE_FAILED,
            reason="negative_value",
            label=label,
            rejected_value=value,
        )
        msg = f"{label} must be non-negative, got {value!r}"
        raise ValueError(msg)


VALID_VERDICTS: Final[frozenset[str]] = frozenset(
    {"allow", "deny", "escalate", "output_scan"}
)
VALID_TOKEN_DIRECTIONS: Final[frozenset[str]] = frozenset({"input", "output"})
VALID_TASK_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"succeeded", "failed", "cancelled"}
)
VALID_TOOL_OUTCOMES: Final[frozenset[str]] = frozenset({"success", "error", "timeout"})
VALID_STATUS_CLASSES: Final[frozenset[str]] = frozenset(
    {"1xx", "2xx", "3xx", "4xx", "5xx"}
)
VALID_AUDIT_APPEND_STATUSES: Final[frozenset[str]] = frozenset(
    {"signed", "fallback", "error"}
)
VALID_OTLP_KINDS: Final[frozenset[str]] = frozenset({"logs", "traces"})
VALID_OTLP_OUTCOMES: Final[frozenset[str]] = frozenset({"success", "failure"})
VALID_IDENTITY_CHANGE_TYPES: Final[frozenset[str]] = frozenset(
    {"created", "updated", "rolled_back", "archived"}
)
VALID_WORKFLOW_EXECUTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "timeout"}
)
# Provider error classes: map every ``ProviderError`` subclass to one
# of these bounded buckets via ``synthorg.providers.errors.classify_provider_error``.
# Unknown exceptions fall into ``"other"`` rather than inflating cardinality.
# Derived from the ``ProviderErrorLabel`` Literal so the two stay in
# lockstep -- adding a new label in one place is enough.
VALID_PROVIDER_ERROR_CLASSES: Final[frozenset[str]] = frozenset(
    get_args(ProviderErrorLabel)
)
# In-process cache names that emit ``synthorg_cache_operations_total``.
# Expanding this set requires adding a new cache + its record call.
VALID_CACHE_NAMES: Final[frozenset[str]] = frozenset({"mcp_result", "reranker"})
VALID_CACHE_OUTCOMES: Final[frozenset[str]] = frozenset({"hit", "miss", "evict"})
# API error classification: derived from ``synthorg.api.errors.ErrorCategory``
# so the metric partitions structured 4xx/5xx responses by their RFC 9457
# category without hand-maintaining a parallel allowlist.
VALID_API_ERROR_CATEGORIES: Final[frozenset[str]] = frozenset(
    e.value for e in ErrorCategory
)


def status_class(status_code: int) -> str:
    """Map an HTTP status code to its ``Nxx`` class label.

    Returns a string outside :data:`VALID_STATUS_CLASSES` on
    out-of-range input so the caller's guard raises clearly rather
    than silently bucketing garbage into ``"5xx"``.
    """
    if 100 <= status_code < 600:  # noqa: PLR2004
        return f"{status_code // 100}xx"
    return "invalid"
