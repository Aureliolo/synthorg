# module-kind: code
"""Bounded label vocabularies that fold unknown values to a sentinel.

These labels arrive from attacker-controllable or open-vocabulary sources
(agent status / trust level from the registry, HTTP method from the request
line, auth-failure ``reason`` from many call sites, task status from a
transition). Rather than reject an out-of-vocabulary value, each ``fold_*``
helper maps it to a sentinel so a novel value cannot mint an unbounded
per-series cardinality explosion.

Kept separate from :mod:`synthorg.observability.prometheus_labels` (which
carries the reject-on-unknown vocabularies validated via ``require_label``) so
the two cardinality policies -- fold vs reject -- stay visibly distinct.
"""

from typing import Final

from synthorg.core.task_enums import TaskStatus

# Agent gauge label vocabularies for ``synthorg_active_agents_total``.
# Both mirror enums (``synthorg.hr.enums.AgentStatus`` /
# ``synthorg.core.tool_constraints.ToolAccessLevel``); they are duplicated
# here as literals rather than imported to keep this module free of an
# ``hr`` / ``tool_constraints`` import (the latter imports ``observability``
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

# Bounded HTTP-method vocabulary for ``synthorg_api_request_duration_seconds``.
# The
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
