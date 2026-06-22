"""Bounded label value sets + helpers for the Prometheus collector.

Every free-form label on a metric is validated against a frozenset
here so a bad call site fails loudly at push time instead of
silently polluting cardinality. The sets and the :func:`_status_class`
helper live in their own module so :mod:`synthorg.observability.prometheus_collector`
stays below the 800-line limit mandated by ``CLAUDE.md``.
"""

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Final, get_args

from synthorg.core.error_taxonomy import ErrorCategory
from synthorg.core.task_enums import TaskStatus
from synthorg.observability import get_logger
from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.providers.errors import ProviderErrorLabel

logger = get_logger(__name__)


def require_label(label: str, value: str, valid: frozenset[str]) -> None:
    """Raise ``ValueError`` if *value* is not in the allowed set.

    Emits a ``WARNING`` log with the rejected value before raising
    so a misbehaving call site is visible in monitoring -- a bare
    ``ValueError`` at the raise site would be invisible unless
    every caller logged it themselves.

    Intended for *bounded-vocabulary* labels (outcomes, verdicts,
    transports, ...): the WARN payload includes the full sorted
    allowed set so an operator can see the expected vocabulary
    inline. For registry-bound labels (agent_ids,
    workflow_definition_ids, departments) where the allowlist can
    grow to hundreds of entries, use :func:`require_label_summary`
    instead -- sorting and serializing the full set on every
    rejection is wasteful at scale.

    Raises:
        ValueError: If *value* is not a member of *valid*.
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


def require_label_summary(label: str, value: str, valid: frozenset[str]) -> None:
    """Raise ``ValueError`` if *value* is not in *valid*; O(1) on rejection.

    Variant of :func:`require_label` for high-cardinality
    registry-bound labels. The WARN payload carries only
    ``len(valid)`` instead of ``sorted(valid)``, and the
    ``ValueError`` message references the count rather than dumping
    the entire allowlist. The membership check itself is already
    O(1) on a ``frozenset``; this avoids the O(n log n) sort + the
    serialization cost of the full set on the rare unknown-label
    path, which would otherwise scale with registry size on every
    rejection.

    Raises:
        ValueError: If *value* is not a member of *valid*.
    """
    if value not in valid:
        logger.warning(
            METRICS_SCRAPE_FAILED,
            reason="invalid_label",
            label=label,
            rejected_value=value,
            allowlist_size=len(valid),
        )
        msg = (
            f"Unknown {label} {value!r}; "
            f"not in registry-bound allowlist (size={len(valid)})"
        )
        raise ValueError(msg)


def require_finite(label: str, value: float | int) -> None:
    """Raise ``ValueError`` if *value* is NaN or infinite.

    Prometheus will happily store NaN/Inf, but dashboards that rely
    on rate() or quantile aggregations break silently when they
    arrive, so every numeric input goes through this guard before
    hitting ``Counter.inc()`` / ``Histogram.observe()``.

    Raises:
        ValueError: If *value* is NaN or infinite.
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

    Raises:
        ValueError: If *value* is negative, NaN, or infinite.
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


# Agent gauge label vocabularies for ``synthorg_active_agents_total``.
# Both mirror enums (``synthorg.hr.enums.AgentStatus`` /
# ``synthorg.core.tool_constraints.ToolAccessLevel``); they are duplicated
# here as literals rather than imported to keep ``prometheus_labels`` free of
# an ``hr`` / ``tool_constraints`` import (the latter imports ``observability``
# and would risk a cold-import cycle). A parity test under
# ``tests/unit/observability/`` asserts the two stay in lockstep. An
# out-of-vocabulary value folds to :data:`AGENT_LABEL_OTHER` rather than
# minting a new series.
VALID_AGENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"active", "onboarding", "on_leave", "terminated"}
)
VALID_TRUST_LEVELS: Final[frozenset[str]] = frozenset(
    {"sandboxed", "restricted", "standard", "elevated", "custom"}
)
AGENT_LABEL_OTHER: Final[str] = "other"

# Bounded HTTP-method vocabulary for ``synthorg_api_requests_total``. The
# method arrives from the request line (attacker-controllable), so an
# unrecognised verb folds to :data:`HTTP_METHOD_OTHER` rather than minting an
# unbounded per-method series.
VALID_HTTP_METHODS: Final[frozenset[str]] = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
)
HTTP_METHOD_OTHER: Final[str] = "__other__"


def fold_agent_status(value: str) -> str:
    """Return *value* if a known agent status, else :data:`AGENT_LABEL_OTHER`.

    Returns:
        The bounded status label.
    """
    return value if value in VALID_AGENT_STATUSES else AGENT_LABEL_OTHER


def fold_trust_level(value: str) -> str:
    """Return *value* if a known trust level, else :data:`AGENT_LABEL_OTHER`.

    Returns:
        The bounded trust-level label.
    """
    return value if value in VALID_TRUST_LEVELS else AGENT_LABEL_OTHER


def fold_http_method(value: str) -> str:
    """Return *value* if a known HTTP method, else :data:`HTTP_METHOD_OTHER`.

    Returns:
        The bounded method label.
    """
    return value if value in VALID_HTTP_METHODS else HTTP_METHOD_OTHER


# Bounded ``reason`` vocabulary for ``synthorg_auth_failures_total``. Auth
# failures are logged at many call sites with free-form ``reason=`` strings;
# the metric folds anything outside this set to :data:`AUTH_FAILURE_OTHER` so
# a new log reason cannot mint an unbounded series.
VALID_AUTH_FAILURE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "invalid_password",
        "hash_verification_error",
        "jwt_secret_missing",
        "token_expired",
        "token_invalid",
        "refresh_rejected",
        "account_locked",
        "unauthenticated",
    }
)
AUTH_FAILURE_OTHER: Final[str] = "__other__"

# Task status labels for ``synthorg_task_transitions_total``; derived from
# ``TaskStatus`` so the two stay in lockstep without a hand-maintained list.
VALID_TASK_STATUSES: Final[frozenset[str]] = frozenset(s.value for s in TaskStatus)


def fold_auth_failure_reason(value: str) -> str:
    """Return *value* if a known auth-failure reason, else the sentinel.

    Returns:
        The bounded reason label (:data:`AUTH_FAILURE_OTHER` when unknown).
    """
    return value if value in VALID_AUTH_FAILURE_REASONS else AUTH_FAILURE_OTHER


VALID_VERDICTS: Final[frozenset[str]] = frozenset(
    {"allow", "deny", "escalate", "output_scan"}
)
VALID_TOKEN_DIRECTIONS: Final[frozenset[str]] = frozenset({"input", "output"})
VALID_PROVIDER_CALL_TYPES: Final[frozenset[str]] = frozenset({"complete", "stream"})
VALID_AUTONOMY_PROMOTION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"granted", "denied"}
)
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
VALID_LOG_SINK_KINDS: Final[frozenset[str]] = frozenset({"http", "syslog"})
VALID_LOG_SINK_OUTCOMES: Final[frozenset[str]] = frozenset({"success", "failure"})
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
#
# Transient vs non-transient mapping for SLO queries:
#   transient: rate_limit, timeout, connection, internal
#   non-transient: invalid_request, auth, content_filter, not_found, other
# PromQL example for transient-error rate:
#   sum(rate(synthorg_provider_errors_total{
#       error_class=~"rate_limit|timeout|connection|internal"}[5m]))
VALID_PROVIDER_ERROR_CLASSES: Final[frozenset[str]] = frozenset(
    get_args(ProviderErrorLabel)
)
TRANSIENT_PROVIDER_ERROR_CLASSES: Final[frozenset[str]] = frozenset(
    {"rate_limit", "timeout", "connection", "internal"},
)
"""Subset of :data:`VALID_PROVIDER_ERROR_CLASSES` that mark transient
failures (caller should retry).  Mirrors
``ProviderError.is_retryable=True`` in :mod:`synthorg.providers.errors`."""

# Fail fast at import time if the transient set drifts out of the canonical
# valid-class allowlist.  Without this guard, a renamed or removed label in
# ``ProviderErrorLabel`` would silently leave a stale entry here that no
# label-validation pipeline consults.
_transient_diff = TRANSIENT_PROVIDER_ERROR_CLASSES - VALID_PROVIDER_ERROR_CLASSES
if _transient_diff:
    msg = (
        "TRANSIENT_PROVIDER_ERROR_CLASSES contains labels not in "
        f"VALID_PROVIDER_ERROR_CLASSES: {sorted(_transient_diff)}"
    )
    raise ValueError(msg)
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

    Returns:
        The ``Nxx`` class string (e.g. ``"2xx"``) for codes 100-599, or
        ``"invalid"`` for out-of-range input.
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

# -- Approval / escalation / blueprint / settings / MCP / budget / audit ---
# Bounded label vocabularies for the secondary-domain observability
# metrics (approvals, escalations, blueprint instantiations, settings
# mutations, MCP handler outcomes, budget queries, audit-chain
# integrity verifications). The settings namespace allowlist mirrors
# the filenames in ``src/synthorg/settings/definitions/`` -- a parity
# test under ``tests/unit/observability/`` asserts the two stay in
# lockstep so adding a new namespace fails fast in tests.
VALID_APPROVAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"approved", "rejected", "expired"}
)
VALID_ESCALATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "resolved",
        "escalated_to_human",
        "auto_resolved",
        "notify_failed",
        "sweeper_failed",
    }
)
VALID_BLUEPRINT_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"success", "validation_error", "not_found", "unknown_error"}
)
VALID_PUSH_QUEUE_OUTCOMES: Final[frozenset[str]] = frozenset({"enqueued", "merged"})
VALID_SETTINGS_NAMESPACES: Final[frozenset[str]] = frozenset(
    {
        "a2a",
        "api",
        "backup",
        "budget",
        "charter",
        "client",
        "cockpit",
        "communication",
        "company",
        "coordination",
        "demo",
        "engine",
        "external_api",
        "hr",
        "integrations",
        "memory",
        "meta",
        "notifications",
        "objectives",
        "observability",
        "providers",
        "research",
        "security",
        "settings",
        "simulations",
        "telemetry",
        "tools",
        "workers",
    }
)
VALID_MCP_HANDLER_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "success",
        "error",
        "validation_error",
        "guardrail_violated",
        "not_found",
        "capability_unsupported",
    }
)
VALID_BUDGET_QUERY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "balance",
        "available_spend",
        "burn_rate",
        "daily_spend",
        "cost_summary",
        "total_cost",
        "agent_cost",
        "project_cost",
    }
)
VALID_AUDIT_VERIFICATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"valid", "broken"}
)
VALID_WS_TRANSPORTS: Final[frozenset[str]] = frozenset({"websocket", "sse"})
VALID_WS_REVALIDATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"pass", "fail", "budget_exhausted"}
)
VALID_PG_BACKENDS: Final[frozenset[str]] = frozenset({"primary", "replica"})


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
# empty (every ``*_seeded`` flag ``False``); ``validate_*`` calls reach
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

    Per-source readiness is tracked with one boolean per registry
    so a transient workflow-repository or department-service outage
    does not suppress the unrelated agent-id allowlist (or vice
    versa).
    """

    agent_ids: frozenset[str] = frozenset()
    workflow_definition_ids: frozenset[str] = frozenset()
    departments: frozenset[str] = frozenset()
    tool_names: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    model_ids: frozenset[str] = frozenset()
    agent_ids_seeded: bool = False
    workflow_definition_ids_seeded: bool = False
    departments_seeded: bool = False
    tool_names_seeded: bool = False
    providers_seeded: bool = False
    model_ids_seeded: bool = False


_INITIAL_SNAPSHOT: Final[_LabelSnapshot] = _LabelSnapshot()
_snapshot: _LabelSnapshot = _INITIAL_SNAPSHOT

# Process-global lock guarding the read/merge/write critical section
# in ``PrometheusCollector._rebuild_label_snapshot``. The lock lives
# next to the ``_snapshot`` it protects (rather than as a per-collector
# attribute) so that two distinct ``PrometheusCollector`` instances --
# which can coexist during tests or in a misconfigured AppState --
# still cannot interleave their fetches with one another's update and
# clobber a partial-failure carry-forward. Validators do NOT take this
# lock: they read the module global once into a local before consulting
# its fields, so a concurrent ``update_label_snapshot()`` either lands
# before or after the local capture, never producing a torn read.
#
# Not ``Final``: ``_reset_label_snapshot_for_tests`` rebinds it so a
# per-test event loop (the unit harness tears one down per test) never
# inherits a lock bound to a closed loop from an earlier test.
_snapshot_lock: asyncio.Lock = asyncio.Lock()


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
    """Reset to bootstrap mode. Only call from test fixtures.

    Rebinds ``_snapshot_lock`` as well so the next test acquires a
    fresh lock on its own event loop rather than one left bound to the
    previous test's torn-down loop.

    Must be called only when no coroutine is inside the lock (i.e. from
    synchronous fixture setup/teardown, never mid-flight with live
    tasks): rebinding while a holder is active would let a new waiter
    acquire the fresh lock and bypass mutual exclusion.
    """
    global _snapshot, _snapshot_lock  # noqa: PLW0603
    _snapshot = _INITIAL_SNAPSHOT
    _snapshot_lock = asyncio.Lock()


def _snapshot_for_collector() -> _LabelSnapshot:
    """Return the current snapshot reference for use by ``PrometheusCollector``.

    Wrapper kept private (underscore prefix) to make the access path
    auditable: only the collector's ``_rebuild_label_snapshot`` should
    read the existing snapshot when computing a partial-failure
    fallback. All other consumers go through the public
    ``validate_*`` / ``is_known_agent_id`` helpers.
    """
    return _snapshot


def _snapshot_lock_for_collector() -> asyncio.Lock:
    """Return the live snapshot lock for ``PrometheusCollector``.

    Accessed through a function (not a value import) so that a
    ``_reset_label_snapshot_for_tests`` rebind reaches the collector:
    a ``from ... import _snapshot_lock`` would freeze the collector on
    the import-time lock and miss the per-test reset.
    """
    return _snapshot_lock


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
    require_label_summary("agent_id", value, snapshot.agent_ids)


def validate_workflow_definition_id(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a known workflow definition.

    Fails closed in every state (see :func:`validate_agent_id` for
    the bootstrap rationale).
    """
    snapshot = _snapshot
    require_label_summary(
        "workflow_definition_id", value, snapshot.workflow_definition_ids
    )


def validate_department(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a known department.

    Fails closed in every state (see :func:`validate_agent_id` for
    the bootstrap rationale).
    """
    snapshot = _snapshot
    require_label_summary("department", value, snapshot.departments)


def validate_tool_name(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a registered tool name.

    Bounds the ``tool_name`` Prometheus label against the running
    ToolRegistry so plugin-loaded tools are accepted but a runaway
    caller that fabricates names cannot inflate cardinality. Fails
    closed during bootstrap (no snapshot seeded yet); push-time
    callers go through ``metrics_hub._safe_record`` so the rejected
    sample drops cleanly.
    """
    snapshot = _snapshot
    require_label_summary("tool_name", value, snapshot.tool_names)


UNKNOWN_LABEL: Final[str] = "__unknown__"

_MODEL_LABEL_MAX_LENGTH: Final[int] = 128
_MODEL_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:/-]+\Z")


def normalize_provider_label(value: str) -> str:
    """Return *value* if a registered provider, else :data:`UNKNOWN_LABEL`.

    Bounds the ``provider`` Prometheus label against the configured
    ``ProviderRegistry`` snapshot. Provider names come from operator
    config (free-form display strings), so without this fold a renamed
    or fat-fingered provider would mint a permanent time-series child.
    Unlike the ``validate_*`` helpers this folds rather than raises:
    provider/usage error metrics must still record under an aggregate
    bucket for a misconfigured provider. Fails closed during bootstrap
    (no snapshot seeded yet); the next scrape rotates the snapshot and
    subsequent samples land under the real provider name.
    """
    snapshot = _snapshot
    if snapshot.providers_seeded and value in snapshot.providers:
        return value
    return UNKNOWN_LABEL


def normalize_model_label(value: str) -> str:
    """Return *value* if a well-formed model id, else :data:`UNKNOWN_LABEL`.

    Unlike ``provider``, the ``model`` label cannot be allowlisted: the
    usable model set is the open litellm namespace plus whatever a
    self-hosted server reports at runtime, so the primary guard is a
    length plus charset cap (``provider/model:tag`` forms included)
    rather than an allowlist. An empty, over-long, or out-of-charset
    value folds to UNKNOWN.

    Once the collector has seeded the configured model ids (the bounded
    set the deployment can actually call), an in-charset value outside
    that set also folds to UNKNOWN so a misconfiguration or a rogue
    self-hosted id cannot mint unbounded per-model series. Before seeding
    (bootstrap) the charset cap alone applies, so real ids are not gutted
    while the snapshot is still empty.
    """
    if not value or len(value) > _MODEL_LABEL_MAX_LENGTH:
        return UNKNOWN_LABEL
    if _MODEL_LABEL_PATTERN.match(value) is None:
        return UNKNOWN_LABEL
    snapshot = _snapshot
    if snapshot.model_ids_seeded and value not in snapshot.model_ids:
        return UNKNOWN_LABEL
    return value


MCP_UNKNOWN_TOOL_LABEL: Final[str] = "__unknown__"

_mcp_tool_names: frozenset[str] = frozenset()


def register_mcp_tool_names(names: frozenset[str]) -> None:
    """Seed the bounded MCP tool-name allowlist for cardinality control.

    The MCP handler registry is closed at startup
    (:meth:`DomainToolRegistry.freeze`); call this once with the
    frozen set of registered tool names so
    :func:`normalize_mcp_tool_label` can substitute
    :data:`MCP_UNKNOWN_TOOL_LABEL` for any caller-supplied tool that
    is not in the registry. Without this seed,
    ``record_mcp_handler_outcome`` would emit arbitrary tool strings
    as Prometheus labels, exploding cardinality on malformed
    requests.
    """
    global _mcp_tool_names  # noqa: PLW0603
    _mcp_tool_names = names


def _reset_mcp_tool_names_for_tests() -> None:
    """Reset the MCP tool-name allowlist to bootstrap. Test-only."""
    global _mcp_tool_names  # noqa: PLW0603
    _mcp_tool_names = frozenset()


def normalize_mcp_tool_label(value: str) -> str:
    """Return *value* if registered, else :data:`MCP_UNKNOWN_TOOL_LABEL`.

    Fails closed, matching :func:`validate_agent_id`: an empty
    allowlist (the invoker has not called
    :func:`register_mcp_tool_names` yet) folds every value to the
    sentinel rather than passing it through. A raw caller-supplied
    tool string reaching the Prometheus label set before the registry
    seeds is the exact cardinality exposure this allowlist exists to
    prevent; the invoker seeds at construction time, so a real metric
    sample is folded to the sentinel only inside that closed bootstrap
    window.
    """
    snapshot = _mcp_tool_names
    if value in snapshot:
        return value
    return MCP_UNKNOWN_TOOL_LABEL


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
