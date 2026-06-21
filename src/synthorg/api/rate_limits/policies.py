"""Per-operation rate-limit and inflight policy registry.

Canonical, single-source-of-truth maps from operation id to:

* ``(max_requests, window_seconds)`` for sliding-window rate limits
  (see :data:`RATE_LIMIT_POLICIES`).
* ``max_inflight`` for concurrency caps (see
  :data:`INFLIGHT_POLICIES`).

Callers build their Litestar guards via
:func:`per_op_rate_limit_from_policy` and the route ``opt`` annotation
via :func:`per_op_concurrency_from_policy` rather than duplicating the
tuple / integer at every decorator site.

Operator overrides continue to flow through the dedicated config
surfaces:

* :class:`synthorg.api.rate_limits.config.PerOpRateLimitConfig.overrides`
  (sliding-window bucket).
* :class:`synthorg.api.rate_limits.inflight_config.PerOpConcurrencyConfig.overrides`
  (inflight bucket).

The registries only replace the inline literals; they are not the
runtime tuning surface.
"""

import copy
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Final

from litestar.connection import ASGIConnection
from litestar.datastructures import State
from litestar.handlers.base import BaseRouteHandler

from synthorg.api.rate_limits._subject import KeyPolicy
from synthorg.api.rate_limits.guard import per_op_rate_limit
from synthorg.api.rate_limits.inflight_guard import per_op_concurrency
from synthorg.observability import get_logger
from synthorg.settings.definitions.api import (
    BROWNFIELD_IMPORT_INFLIGHT_MAX,
    EVENTS_STREAM_INFLIGHT_MAX,
    EVENTS_STREAM_RATE_LIMIT_MAX_REQUESTS,
    EVENTS_STREAM_RATE_LIMIT_WINDOW_SECONDS,
    MEMORY_CHECKPOINT_DEPLOY_INFLIGHT_MAX,
    MEMORY_CHECKPOINT_ROLLBACK_INFLIGHT_MAX,
    MEMORY_FINE_TUNE_INFLIGHT_MAX,
    PROVIDERS_DISCOVER_MODELS_INFLIGHT_MAX,
    PROVIDERS_PULL_MODEL_INFLIGHT_MAX,
)

logger = get_logger(__name__)


# Every rate-limited endpoint registered here.  Keys are stable,
# human-readable operation ids of the form ``<domain>.<action>``.
# Values are ``(max_requests, window_seconds)`` -- the defaults a
# fresh deployment ships with.  Rows are grouped by controller family
# and sorted alphabetically so diffs stay focused and conflicts
# (a typoed duplicate key) fail at import via ruff ``F601``.
_POLICIES: Final[dict[str, tuple[int, int]]] = {
    # a2a
    "a2a.gateway": (120, 60),
    # admin (backup controller)
    "admin.backup_create": (5, 3600),
    "admin.backup_delete": (10, 3600),
    "admin.backup_restore": (3, 3600),
    # agents
    "agents.autonomy_change": (10, 60),
    "agents.create": (10, 60),
    "agents.delete": (5, 60),
    "agents.update": (20, 60),
    # analytics
    "analytics.forecast": (30, 60),
    "analytics.overview": (30, 60),
    "analytics.trends": (30, 60),
    # approvals
    "approvals.approve": (100, 60),
    "approvals.create": (20, 60),
    "approvals.reject": (100, 60),
    # artifacts
    "artifacts.create": (60, 60),
    "artifacts.download": (60, 60),
    "artifacts.upload": (10, 60),
    # auth
    "auth.sessions_list": (30, 60),
    "auth.sessions_revoke": (60, 60),
    "auth.ws_ticket": (20, 60),
    "auth.api_keys_issue": (10, 60),
    "auth.api_keys_list": (30, 60),
    "auth.api_keys_revoke": (30, 60),
    # brain (project brain)
    "brain.search": (30, 60),
    # brownfield
    "brownfield.import": (10, 60),
    # budget (forecast controller)
    "budget.forecast_create": (5, 60),
    "budget.forecast_decide": (20, 60),
    "budget.forecast_raise_ceiling": (10, 60),
    # budget (CFO cost-optimizer controller)
    "budget.cfo_anomalies": (30, 60),
    "budget.cfo_efficiency": (30, 60),
    # clients
    "clients.create": (10, 60),
    # cockpit
    "cockpit.intervention_kill": (10, 60),
    "cockpit.intervention_pause": (30, 60),
    # collaboration
    "collaboration.override": (20, 60),
    # company
    "company.reorder_departments": (10, 60),
    "company.update": (20, 60),
    # connections
    "connections.create": (20, 60),
    "connections.delete": (10, 60),
    "connections.update": (30, 60),
    # coordination
    "coordination.metrics_query": (30, 60),
    # custom_rules
    "custom_rules.create": (20, 60),
    "custom_rules.delete": (20, 60),
    "custom_rules.preview": (30, 60),
    "custom_rules.toggle": (30, 60),
    "custom_rules.update": (30, 60),
    # decomposition (manual task decomposition)
    "decomposition.manual": (20, 60),
    # departments
    "departments.create": (10, 60),
    "departments.delete": (5, 60),
    "departments.delete_ceremony_policy": (10, 60),
    "departments.reorder_agents": (30, 60),
    "departments.update": (20, 60),
    "departments.update_ceremony_policy": (20, 60),
    # docs (project docs)
    "docs.search": (30, 60),
    # escalations
    "escalations.cancel": (30, 60),
    "escalations.decide": (30, 60),
    "escalations.get": (120, 60),
    "escalations.list": (120, 60),
    # events
    "events.stream": (
        EVENTS_STREAM_RATE_LIMIT_MAX_REQUESTS,
        EVENTS_STREAM_RATE_LIMIT_WINDOW_SECONDS,
    ),
    # integrations (health controller)
    "integrations.health_aggregate": (30, 60),
    "integrations.health_single": (60, 60),
    # interrupts
    "interrupts.resume": (60, 60),
    # knowledge
    "knowledge.search": (30, 60),
    # learning
    "learning.curve": (30, 60),
    # meetings
    "meetings.create": (20, 60),
    # messages
    "messages.delete": (100, 3600),
    # meta
    "meta.chat": (5, 60),
    "meta.chat.act": (5, 60),
    "meta.chat.group": (5, 60),
    "meta.chat.propose": (5, 60),
    "meta.charters.interview": (10, 60),
    "meta.charters.approve": (5, 60),
    "meta.charters.edit": (20, 60),
    "meta.charters.cancel": (10, 60),
    "meta.ingest_events": (60, 60),
    "meta.trigger_cycle": (1, 60),
    # memory
    "memory.checkpoint_delete": (20, 60),
    "memory.checkpoint_deploy": (2, 3600),
    "memory.checkpoint_rollback": (2, 3600),
    "memory.entry_delete": (60, 60),
    "memory.fine_tune": (2, 3600),
    "memory.fine_tune_cancel": (10, 3600),
    "memory.fine_tune_preflight": (50, 60),
    "memory.fine_tune_resume": (5, 3600),
    # oauth
    "oauth.callback": (30, 60),
    "oauth.initiate": (10, 60),
    # ontology
    "ontology.admin_derive": (5, 60),
    "ontology.admin_sync_org_memory": (5, 60),
    "ontology.create_entity": (20, 60),
    "ontology.delete_entity": (10, 60),
    "ontology.drift_check": (5, 60),
    "ontology.update_entity": (30, 60),
    # objectives
    "objectives.submit": (30, 60),
    # personalities
    "personalities.create": (20, 60),
    "personalities.delete": (10, 60),
    "personalities.update": (30, 60),
    # projects
    "projects.create": (10, 60),
    "projects.delete": (5, 60),
    # promotion
    "promotion.apply": (20, 60),
    "promotion.trigger_cycle": (5, 60),
    # providers
    "providers.add_model": (20, 60),
    "providers.allowlist_add": (50, 60),
    "providers.allowlist_remove": (50, 60),
    "providers.create": (10, 60),
    "providers.create_from_preset": (10, 60),
    "providers.delete": (5, 60),
    "providers.delete_model": (20, 60),
    "providers.delete_preset_override": (10, 60),
    "providers.discover_models": (5, 60),
    "providers.list_models": (60, 60),
    "providers.model_refresh_decide": (20, 60),
    "providers.model_refresh_trigger": (2, 300),
    "providers.probe_local": (20, 60),
    "providers.pull_model": (5, 300),
    "providers.rotate_credentials": (5, 60),
    "providers.sync_models": (5, 60),
    "providers.test": (20, 60),
    "providers.update": (20, 60),
    "providers.update_model_config": (50, 60),
    "providers.update_preset_override": (10, 60),
    "providers.update_rate_limits": (20, 60),
    # quality
    "quality.delete_override": (50, 60),
    "quality.override": (50, 60),
    # reports
    "reports.generate": (5, 60),
    # requests
    "requests.approve": (100, 60),
    "requests.create": (30, 60),
    "requests.reject": (100, 60),
    "requests.update_scope": (50, 60),
    # reviews
    "reviews.decide_stage": (50, 60),
    # scaling
    "scaling.trigger_evaluation": (10, 60),
    "scaling.update_priority": (30, 60),
    "scaling.update_strategy": (30, 60),
    # security
    "security.audit_query": (30, 60),
    "security.ssrf_resolve": (20, 60),
    # settings
    "settings.delete": (60, 60),
    "settings.import": (5, 3600),
    "settings.update": (60, 60),
    # setup
    "setup.complete": (5, 3600),
    # simulations
    "simulations.cancel": (30, 60),
    "simulations.create": (30, 3600),
    # subworkflows
    "subworkflows.create": (20, 60),
    "subworkflows.delete_version": (10, 60),
    # experiments (A/B test registry)
    "experiments.register": (50, 60),
    "experiments.assign": (500, 60),
    # tasks
    "tasks.cancel": (50, 60),
    "tasks.coordinate": (10, 60),
    "tasks.create": (50, 60),
    "tasks.delete": (20, 60),
    "tasks.execute": (200, 60),
    "tasks.transition": (100, 60),
    "tasks.update": (100, 60),
    # teams
    "teams.create": (10, 60),
    "teams.delete": (5, 60),
    "teams.reorder": (30, 60),
    "teams.update": (20, 60),
    # training
    "training.create_plan": (30, 3600),
    "training.execute": (20, 3600),
    "training.preview": (30, 3600),
    "training.update_overrides": (60, 3600),
    # users
    "users.create": (5, 60),
    "users.delete": (3, 60),
    "users.grant_org_role": (10, 60),
    "users.revoke_org_role": (10, 60),
    "users.update_role": (10, 60),
    # webhooks
    "webhooks.receive": (120, 60),
    # workflows
    "workflows.activate": (10, 60),
    "workflows.cancel": (50, 60),
    "workflows.create": (20, 60),
    "workflows.create_from_blueprint": (20, 60),
    "workflows.delete": (10, 60),
    "workflows.export": (20, 60),
    "workflows.update": (30, 60),
    "workflows.validate": (30, 60),
    "workflows.validate_draft": (30, 60),
}

RATE_LIMIT_POLICIES: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    copy.deepcopy(_POLICIES),
)
"""Immutable view of the per-operation rate-limit policy registry."""


# Default inflight cap per operation.  Keys are the same stable
# ``<domain>.<action>`` ids used by ``RATE_LIMIT_POLICIES``; values are
# the maximum concurrent in-flight requests per subject (per-user
# bucket by default).  Two routes that share an operation id share one
# inflight bucket -- e.g. ``memory.fine_tune`` covers both ``start``
# and ``resume`` so a user cannot resume while a fresh start is still
# pending.  Operators override per deployment via
# ``PerOpConcurrencyConfig.overrides``; this map is the default that
# ships with a fresh deployment.
_INFLIGHT_POLICIES: Final[dict[str, int]] = {
    "brownfield.import": BROWNFIELD_IMPORT_INFLIGHT_MAX,
    "events.stream": EVENTS_STREAM_INFLIGHT_MAX,
    "memory.checkpoint_deploy": MEMORY_CHECKPOINT_DEPLOY_INFLIGHT_MAX,
    "memory.checkpoint_rollback": MEMORY_CHECKPOINT_ROLLBACK_INFLIGHT_MAX,
    "memory.fine_tune": MEMORY_FINE_TUNE_INFLIGHT_MAX,
    "providers.discover_models": PROVIDERS_DISCOVER_MODELS_INFLIGHT_MAX,
    "providers.pull_model": PROVIDERS_PULL_MODEL_INFLIGHT_MAX,
}

INFLIGHT_POLICIES: Final[Mapping[str, int]] = MappingProxyType(
    copy.deepcopy(_INFLIGHT_POLICIES),
)
"""Immutable view of the per-operation inflight policy registry."""


def per_op_rate_limit_from_policy(
    operation: str,
    *,
    key: KeyPolicy = "user_or_ip",
) -> Callable[
    [ASGIConnection[object, object, object, State], BaseRouteHandler],
    Awaitable[None],
]:
    """Build a Litestar guard for ``operation`` using the policy registry.

    Args:
        operation: Stable operation id.  Must be a key in
            :data:`RATE_LIMIT_POLICIES`.
        key: Subject bucketing policy (forwarded verbatim to the
            underlying :func:`per_op_rate_limit` decorator).

    Returns:
        A Litestar-compatible async guard with the registry defaults
        applied.

    Raises:
        KeyError: When ``operation`` is not registered.  This is a
            programming error -- registering a new decorator site
            without adding a policy row fails loud at import time.
    """
    try:
        max_requests, window_seconds = RATE_LIMIT_POLICIES[operation]
    except KeyError:
        msg = (
            f"No rate-limit policy registered for {operation!r}. "
            "Add an entry to RATE_LIMIT_POLICIES in "
            "synthorg.api.rate_limits.policies."
        )
        raise KeyError(msg) from None
    return per_op_rate_limit(
        operation,
        max_requests=max_requests,
        window_seconds=window_seconds,
        key=key,
    )


def per_op_concurrency_from_policy(
    operation: str,
    *,
    key: KeyPolicy = "user",
) -> dict[str, tuple[str, int, KeyPolicy]]:
    """Build the route ``opt`` annotation for ``operation`` from the registry.

    Args:
        operation: Stable operation id.  Must be a key in
            :data:`INFLIGHT_POLICIES`.
        key: Subject keying policy forwarded verbatim to
            :func:`per_op_concurrency`.

    Returns:
        A single-key dict shaped for Litestar's ``opt={}`` argument.

    Raises:
        KeyError: When ``operation`` is not registered.  This is a
            programming error -- registering a new decorator site
            without adding a policy row fails loud at import time.
    """
    try:
        max_inflight = INFLIGHT_POLICIES[operation]
    except KeyError:
        msg = (
            f"No inflight policy registered for {operation!r}. "
            "Add an entry to INFLIGHT_POLICIES in "
            "synthorg.api.rate_limits.policies."
        )
        raise KeyError(msg) from None
    return per_op_concurrency(operation, max_inflight=max_inflight, key=key)
