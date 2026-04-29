"""Bounded label value sets + helpers for the Prometheus collector.

Every free-form label on a metric is validated against a frozenset
here so a bad call site fails loudly at push time instead of
silently polluting cardinality. The sets and the :func:`_status_class`
helper live in their own module so :mod:`synthorg.observability.prometheus_collector`
stays below the 800-line limit mandated by ``CLAUDE.md``.
"""

import math
from dataclasses import dataclass
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
    "VALID_DISCONNECT_REASONS",
    "VALID_DISCONNECT_TRANSPORTS",
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
    "_LabelSnapshot",
    "_reset_label_snapshot_for_tests",
    "is_known_agent_id",
    "require_finite",
    "require_label",
    "require_non_negative",
    "status_class",
    "update_label_snapshot",
    "validate_agent_id",
    "validate_department",
    "validate_workflow_definition_id",
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
    {"succeeded", "failed", "cancelled", "rejected"}
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
# API error classification: derived from ``synthorg.core.error_taxonomy.ErrorCategory``
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


# -- Client disconnect ------------------------------------------------------
# Bounded transport / reason vocab for ``synthorg_client_disconnects_total``.
# Single counter with two labels (max 12 series) is preferred over per-
# transport counters: one alert rule, one panel, one query path.
VALID_DISCONNECT_TRANSPORTS: Final[frozenset[str]] = frozenset(
    {"sse", "websocket", "mcp_stdio", "mcp_http"}
)
VALID_DISCONNECT_REASONS: Final[frozenset[str]] = frozenset(
    {"client_initiated", "transport_error", "cancelled", "timeout"}
)


# -- Snapshot-backed registry-bound label validation -----------------------
# Push-time ``record_*`` methods on the Prometheus collector are
# synchronous, but the runtime registries that own the truth about
# valid agent / workflow / department label values are async-only and
# lock-guarded. Awaiting from a sync metric path is impossible, so we
# keep a process-global ``_LabelSnapshot`` of the relevant frozensets
# and refresh it from the existing async ``PrometheusCollector.refresh()``
# pre-scrape coroutine. Sync record sites consult this snapshot via the
# ``validate_<label>`` helpers below.
#
# Validators fail closed in every state. The initial snapshot is
# empty + ``seeded=False``; ``validate_*`` calls reach
# :func:`require_label` against an empty frozenset and raise
# ``ValueError`` (logged WARN by :func:`require_label`). Push-time
# callers go through ``metrics_hub._safe_record``, which swallows the
# ``ValueError`` so a rejected sample drops cleanly without crashing
# the business path.
#
# A bootstrap pass-through was considered and rejected: it would let
# arbitrary startup metric labels create permanent Prometheus
# children before ``PrometheusCollector.refresh()`` lands the first
# real snapshot, which is exactly the cardinality bomb this module
# exists to prevent. Cold-start traffic produces WARN logs until the
# first scrape; the next scrape rotates the snapshot and the sample
# lands.
#
# Ephemeral label values that never enter the registries (e.g. a
# test agent created and immediately discarded between scrapes) are
# permanently rejected. This is intentional: cardinality safety
# beats capturing every transient value.


@dataclass(frozen=True, slots=True)
class _LabelSnapshot:
    """Immutable snapshot of bounded values for high-cardinality labels.

    The Prometheus collector's async ``refresh()`` rebuilds this on
    every scrape and hands the new snapshot to
    :func:`update_label_snapshot`. Lag between scrapes (~15s) is
    acceptable: a brand-new agent's first metric may be dropped with a
    WARN, but the next scrape rotates the snapshot and the sample
    lands.
    """

    agent_ids: frozenset[str] = frozenset()
    workflow_definition_ids: frozenset[str] = frozenset()
    departments: frozenset[str] = frozenset()
    seeded: bool = False


_INITIAL_SNAPSHOT: Final[_LabelSnapshot] = _LabelSnapshot()
_snapshot: _LabelSnapshot = _INITIAL_SNAPSHOT


def update_label_snapshot(snapshot: _LabelSnapshot) -> None:
    """Replace the active label snapshot.

    Intended caller: :meth:`PrometheusCollector.refresh` once it has
    queried the registries for live agent ids, workflow definition
    ids, and departments. Rebinding a module global is a single
    atomic bytecode op under the GIL, so concurrent readers either
    see the old or new snapshot reference -- never a torn
    ``(seeded, frozenset)`` pair. The validators below capture the
    reference once into a local before consulting both fields,
    eliminating any read-then-read race even on free-threaded
    builds where the GIL doesn't apply.
    """
    global _snapshot  # noqa: PLW0603
    _snapshot = snapshot


def _reset_label_snapshot_for_tests() -> None:
    """Reset to bootstrap mode. Only call from test fixtures."""
    global _snapshot  # noqa: PLW0603
    _snapshot = _INITIAL_SNAPSHOT


def _snapshot_for_collector() -> _LabelSnapshot:
    """Return the current snapshot reference for use by ``PrometheusCollector``.

    Wrapper kept private (underscore prefix) to make the access path
    auditable: only the collector's ``_rebuild_label_snapshot`` should
    read the existing snapshot when computing a partial-failure
    fallback. All other consumers go through the public
    ``validate_*`` / ``is_known_agent_id`` helpers.
    """
    return _snapshot


def validate_agent_id(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a known agent id.

    Fails closed in every state, including bootstrap mode (no
    :func:`update_label_snapshot` call yet). A bootstrap pass-through
    would let arbitrary startup metric labels create permanent
    Prometheus children before the registry snapshot lands, which is
    exactly the cardinality bomb this module exists to prevent.
    Push-time callers go through ``metrics_hub._safe_record``, which
    swallows the ValueError and emits a WARN, so a rejected sample
    drops cleanly without crashing the business path.
    """
    snapshot = _snapshot
    require_label("agent_id", value, snapshot.agent_ids)


def validate_workflow_definition_id(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a known workflow definition.

    Fails closed in every state (see :func:`validate_agent_id` for
    the bootstrap rationale).
    """
    snapshot = _snapshot
    require_label("workflow_definition_id", value, snapshot.workflow_definition_ids)


def validate_department(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a known department.

    Fails closed in every state (see :func:`validate_agent_id` for
    the bootstrap rationale).
    """
    snapshot = _snapshot
    require_label("department", value, snapshot.departments)


def is_known_agent_id(value: str) -> bool:
    """Return ``True`` if *value* is a known agent id.

    Non-raising counterpart to :func:`validate_agent_id`. Used by the
    async ``refresh()`` path's task-metric loop to drop gauge updates
    for orphan ``task.assigned_to`` references without aborting the
    full refresh. Returns ``False`` during bootstrap (no snapshot
    seeded yet), which makes the task-metric loop emit no per-agent
    labels until ``refresh()`` lands the first snapshot.
    """
    return value in _snapshot.agent_ids
