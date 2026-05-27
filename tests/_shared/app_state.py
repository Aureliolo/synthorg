"""Test helper for building a thin ``AppState`` with services wired into slices.

After the feature-manifest collapse, ``AppState`` holds only
``config`` / ``clock`` / ``startup_time`` + cross-cutting primitives;
every domain service lives on its feature state slice. Test fixtures
that used to construct ``AppState(<45 service kwargs>)`` call
:func:`make_app_state` instead: it builds the thin ``AppState`` and
composes the supplied services into their owning slices via ``wire``,
keeping the old keyword names so call sites only swap the constructor.

All ``synthorg`` imports are function-local: importing the feature
slice modules at module top triggers the cold-import cycle through
``synthorg.core.agent`` (the same one ``discover_features`` warms), so
deferring them to call time keeps ``from tests._shared import
make_app_state`` cheap and cycle-free.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synthorg._core.features import BaseFeatureStateSlice
    from synthorg.api.state import AppState


def make_app_state(
    *,
    slices: Mapping[type[BaseFeatureStateSlice], Mapping[str, Any]] | None = None,
    **overrides: Any,
) -> AppState:
    """Build a thin ``AppState`` with *overrides* composed into their slices.

    ``config`` (default ``RootConfig(company_name="test")``), ``clock``,
    and ``startup_time`` pass straight to the constructor; every other
    keyword is wired into its owning feature slice by the well-known
    ``kwarg_to_slice`` map. ``None`` values are skipped (the slice field
    defaults to ``None`` already), so ``make_app_state(persistence=None)``
    returns a state whose persistence backend is unset.

    For feature slices whose fields are not in the well-known map (e.g.
    the per-domain MCP-handler services), pass *slices*: a mapping of
    slice class to a field-name -> value mapping, wired verbatim. This
    keeps the helper's keyword surface small while letting a fixture be
    explicit about which slice each service lands on.

    Returns:
        The composed ``AppState``.

    Raises:
        KeyError: When an *overrides* keyword does not map to a slice.
    """
    from synthorg.a2a.state import A2aStateSlice
    from synthorg.api.api_core_state import ApiCoreStateSlice
    from synthorg.api.state import AppState
    from synthorg.approval.state import ApprovalStateSlice
    from synthorg.budget.state import BudgetStateSlice
    from synthorg.client.state import ClientStateSlice
    from synthorg.communication.state import (
        CommunicationStateSlice,
    )
    from synthorg.config.schema import RootConfig
    from synthorg.coordination.state import (
        CoordinationStateSlice,
    )
    from synthorg.engine.state import EngineStateSlice
    from synthorg.engine.workspace.state import (
        WorkspaceStateSlice,
    )
    from synthorg.hr.state import HrStateSlice
    from synthorg.integrations.state import (
        IntegrationsStateSlice,
    )
    from synthorg.memory.state import MemoryStateSlice
    from synthorg.meta.state import MetaStateSlice
    from synthorg.notifications.state import (
        NotificationsStateSlice,
    )
    from synthorg.persistence.state import PersistenceStateSlice
    from synthorg.providers.state import ProvidersStateSlice
    from synthorg.security.state import SecurityStateSlice
    from synthorg.settings.state import SettingsStateSlice
    from synthorg.tools.state import ToolsStateSlice
    from synthorg.workers.state import RuntimeStateSlice

    kwarg_to_slice: dict[str, tuple[type[BaseFeatureStateSlice], str]] = {
        "persistence": (PersistenceStateSlice, "backend"),
        "message_bus": (CommunicationStateSlice, "message_bus"),
        "message_service": (CommunicationStateSlice, "message_service"),
        "meeting_orchestrator": (CommunicationStateSlice, "meeting_orchestrator"),
        "meeting_scheduler": (CommunicationStateSlice, "meeting_scheduler"),
        "meeting_service": (CommunicationStateSlice, "meeting_service"),
        "event_stream_hub": (CommunicationStateSlice, "event_stream_hub"),
        "interrupt_store": (CommunicationStateSlice, "interrupt_store"),
        "delegation_record_store": (CommunicationStateSlice, "delegation_record_store"),
        "cost_tracker": (BudgetStateSlice, "cost_tracker"),
        "cost_forecaster": (BudgetStateSlice, "cost_forecaster"),
        "cost_forecast_repo": (BudgetStateSlice, "cost_forecast_repo"),
        "benchmark_provider": (BudgetStateSlice, "benchmark_provider"),
        "budget_config": (BudgetStateSlice, "budget_config"),
        "report_service": (BudgetStateSlice, "report_service"),
        "approval_store": (ApprovalStateSlice, "store"),
        "auth_service": (ApiCoreStateSlice, "auth_service"),
        "session_store": (ApiCoreStateSlice, "session_store"),
        "lockout_store": (ApiCoreStateSlice, "lockout_store"),
        "refresh_store": (ApiCoreStateSlice, "refresh_store"),
        "ticket_store": (ApiCoreStateSlice, "ticket_store"),
        "user_presence": (ApiCoreStateSlice, "user_presence"),
        "org_mutation_service": (ApiCoreStateSlice, "org_mutation_service"),
        "cursor_secret": (ApiCoreStateSlice, "cursor_secret"),
        "task_engine": (EngineStateSlice, "task_engine"),
        "work_pipeline": (EngineStateSlice, "work_pipeline"),
        "ceremony_scheduler": (EngineStateSlice, "ceremony_scheduler"),
        "intake_entry_adapter": (EngineStateSlice, "intake_entry_adapter"),
        "objective_entry_adapter": (EngineStateSlice, "objective_entry_adapter"),
        "brownfield_entry_adapter": (EngineStateSlice, "brownfield_entry_adapter"),
        "task_board_entry_adapter": (EngineStateSlice, "task_board_entry_adapter"),
        "agent_registry": (HrStateSlice, "agent_registry"),
        "performance_tracker": (HrStateSlice, "performance_tracker"),
        "training_service": (HrStateSlice, "training_service"),
        "settings_service": (SettingsStateSlice, "settings_service"),
        "config_resolver": (SettingsStateSlice, "config_resolver"),
        "provider_registry": (ProvidersStateSlice, "registry"),
        "model_router": (ProvidersStateSlice, "model_router"),
        "provider_health_tracker": (ProvidersStateSlice, "health_tracker"),
        "tool_invocation_tracker": (ToolsStateSlice, "invocation_tracker"),
        "artifact_storage": (WorkspaceStateSlice, "artifact_storage"),
        "project_workspace_service": (
            WorkspaceStateSlice,
            "project_workspace_service",
        ),
        "agent_workspace_root": (WorkspaceStateSlice, "agent_workspace_root"),
        "notification_dispatcher": (NotificationsStateSlice, "dispatcher"),
        "audit_log": (SecurityStateSlice, "audit_log"),
        "trust_service": (SecurityStateSlice, "trust_service"),
        "autonomy_change_strategy": (SecurityStateSlice, "autonomy_change_strategy"),
        "coordination_metrics_store": (CoordinationStateSlice, "metrics_store"),
        "connection_catalog": (IntegrationsStateSlice, "connection_catalog"),
        "oauth_token_manager": (IntegrationsStateSlice, "oauth_token_manager"),
        "oauth_state_service": (IntegrationsStateSlice, "oauth_state_service"),
        "health_prober_service": (IntegrationsStateSlice, "health_prober_service"),
        "tunnel_provider": (IntegrationsStateSlice, "tunnel_provider"),
        "webhook_event_bridge": (IntegrationsStateSlice, "webhook_event_bridge"),
        "mcp_catalog_service": (IntegrationsStateSlice, "mcp_catalog_service"),
        "mcp_installations_repo": (IntegrationsStateSlice, "mcp_installations_repo"),
        "memory_backend": (MemoryStateSlice, "backend"),
        "client_simulation_state": (ClientStateSlice, "simulation_state"),
        "a2a_client": (A2aStateSlice, "client"),
        "experiment_service": (MetaStateSlice, "experiment_service"),
        "coordinator": (RuntimeStateSlice, "coordinator"),
        "worker_execution_service": (RuntimeStateSlice, "worker_execution_service"),
    }

    config = overrides.pop("config", None) or RootConfig(company_name="test")
    clock = overrides.pop("clock", None)
    startup_time = overrides.pop("startup_time", None)
    app_state = AppState(config=config, clock=clock, startup_time=startup_time)
    for key, value in overrides.items():
        if value is None:
            continue
        if key not in kwarg_to_slice:
            msg = f"make_app_state: unknown service keyword {key!r}"
            raise KeyError(msg)
        slice_cls, field = kwarg_to_slice[key]
        app_state.wire(slice_cls, **{field: value})
    for slice_cls, fields in (slices or {}).items():
        app_state.wire(slice_cls, **dict(fields))
    return app_state
