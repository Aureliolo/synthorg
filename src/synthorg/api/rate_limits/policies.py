"""Per-operation rate-limit policy registry.

Canonical, single-source-of-truth map from operation id to the default
``(max_requests, window_seconds)`` tuple each controller endpoint uses.
Callers build their Litestar guard via
:func:`per_op_rate_limit_from_policy` rather than duplicating the
tuple at every decorator site.

Operator overrides continue to flow through
:class:`synthorg.api.rate_limits.config.PerOpRateLimitConfig.overrides`
(CFG-1) - the registry only replaces the inline literals, it is not
the runtime tuning surface.
"""

import copy
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from synthorg.api.rate_limits._subject import KeyPolicy  # noqa: TC001
from synthorg.api.rate_limits.guard import per_op_rate_limit
from synthorg.observability import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler


# Every rate-limited endpoint registered here.  Keys are stable,
# human-readable operation ids of the form ``<domain>.<action>``.
# Values are ``(max_requests, window_seconds)`` -- the defaults a
# fresh deployment ships with.  Rows are grouped by controller family
# and sorted alphabetically so diffs stay focused and conflicts
# (a typoed duplicate key) fail at import via ruff ``F601``.
_POLICIES: Final[dict[str, tuple[int, int]]] = {
    # admin (backup controller)
    "admin.backup_restore": (3, 3600),
    # agents
    "agents.create": (10, 60),
    "agents.delete": (5, 60),
    "agents.update": (20, 60),
    # approvals
    "approvals.approve": (100, 60),
    "approvals.create": (20, 60),
    "approvals.reject": (100, 60),
    # artifacts
    "artifacts.upload": (10, 60),
    # connections
    "connections.create": (20, 60),
    "connections.delete": (10, 60),
    "connections.update": (30, 60),
    # custom_rules
    "custom_rules.create": (20, 60),
    "custom_rules.delete": (20, 60),
    "custom_rules.preview": (30, 60),
    "custom_rules.toggle": (30, 60),
    "custom_rules.update": (30, 60),
    # departments
    "departments.create": (10, 60),
    "departments.delete": (5, 60),
    "departments.delete_ceremony_policy": (10, 60),
    "departments.reorder_agents": (30, 60),
    "departments.update": (20, 60),
    "departments.update_ceremony_policy": (20, 60),
    # escalations
    "escalations.cancel": (30, 60),
    "escalations.decide": (30, 60),
    "escalations.get": (120, 60),
    "escalations.list": (120, 60),
    # meetings
    "meetings.create": (20, 60),
    # meta
    "meta.chat": (5, 60),
    # memory
    "memory.checkpoint_delete": (20, 60),
    "memory.checkpoint_deploy": (2, 3600),
    "memory.entry_delete": (60, 60),
    "memory.checkpoint_rollback": (2, 3600),
    "memory.fine_tune": (2, 3600),
    "memory.fine_tune_cancel": (10, 3600),
    "memory.fine_tune_preflight": (50, 60),
    "memory.fine_tune_resume": (5, 3600),
    # oauth
    "oauth.callback": (30, 60),
    # ontology
    "ontology.admin_derive": (5, 60),
    "ontology.admin_sync_org_memory": (5, 60),
    "ontology.create_entity": (20, 60),
    "ontology.delete_entity": (10, 60),
    "ontology.drift_check": (5, 60),
    "ontology.update_entity": (30, 60),
    # personalities
    "personalities.create": (20, 60),
    "personalities.delete": (10, 60),
    "personalities.update": (30, 60),
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
    # settings
    "settings.delete": (60, 60),
    "settings.update": (60, 60),
    # setup
    "setup.complete": (5, 3600),
    # simulations
    "simulations.create": (30, 3600),
    # tasks
    "tasks.cancel": (50, 60),
    "tasks.create": (50, 60),
    "tasks.delete": (20, 60),
    "tasks.transition": (100, 60),
    "tasks.update": (100, 60),
    # training
    "training.execute": (20, 3600),
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
    "workflows.update": (30, 60),
}

RATE_LIMIT_POLICIES: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    copy.deepcopy(_POLICIES),
)
"""Immutable view of the per-operation rate-limit policy registry."""


def per_op_rate_limit_from_policy(
    operation: str,
    *,
    key: KeyPolicy = "user_or_ip",
) -> Callable[
    [ASGIConnection[Any, Any, Any, Any], BaseRouteHandler],
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
