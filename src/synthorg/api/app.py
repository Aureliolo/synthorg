"""Litestar application factory.

Creates and configures the Litestar application with all
controllers, middleware, exception handlers, plugins, and
lifecycle hooks (startup/shutdown).
"""

import sys

from litestar import Litestar, Router

from synthorg.api.app_builders import (
    _bootstrap_app_logging,
)
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.auth.controller_helpers import require_password_changed
from synthorg.api.boot_persistence import resolve_boot_persistence
from synthorg.api.construction_phase import build_construction_services
from synthorg.api.feature_composition import (
    collect_route_handlers,
)
from synthorg.api.lifecycle_assembly import assemble_lifespan_hooks
from synthorg.api.litestar_assembly import build_litestar
from synthorg.config.schema import RootConfig
from synthorg.observability import (
    get_logger,
    safe_error_description,
)

logger = get_logger(__name__)


# Construction bakes immutable middleware / CORS / routes from RootConfig;
# on_startup wires SettingsService + ConfigResolver for runtime-editable
# settings. Litestar rate-limit middleware reads config at construction;
# runtime DB changes only affect code calling get_api_config(). Boot-time
# setting resolvers + the default approval-timeout scheduler live in
# ``lifecycle_helpers/boot_resolvers.py``.


def create_app(
    *,
    config: RootConfig | None = None,
    overrides: AppOverrides | None = None,
    _skip_lifecycle_shutdown: bool = False,
) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        config: Root company configuration.
        overrides: Optional dependency injections (chiefly tests / bespoke
            wiring); any field left unset is auto-wired from config and the
            environment. An injected double always wins over the auto-wired one.
        _skip_lifecycle_shutdown: Test-only flag. When ``True`` the app is built
            with an empty ``on_shutdown`` list so a shared-app fixture can reuse
            it across lifespans without tearing down the task engine, message
            bus, and persistence. Never use in production: shutdown hooks
            perform critical cleanup.

    Returns:
        Configured Litestar application.
    """
    ov = overrides or AppOverrides()
    effective_config = config or RootConfig(company_name="default")

    # Activate the structured logging pipeline before any
    # other setup so that auto-wiring, persistence, and bus logs all
    # flow through the configured sinks.  Respects SYNTHORG_LOG_DIR
    # env var for Docker log directory override.
    try:
        effective_config = _bootstrap_app_logging(effective_config)
    except Exception as exc:
        print(  # noqa: T201
            f"CRITICAL: Failed to initialise logging pipeline: {safe_error_description(exc)}. "  # noqa: E501
            "Check SYNTHORG_LOG_DIR, SYNTHORG_LOG_LEVEL, and the "
            "'logging' section of your config file.",
            file=sys.stderr,
            flush=True,
        )
        raise

    api_config = effective_config.api

    # Auto-wire persistence + artifact storage from the CLI-provided env vars
    # (unless injected); the raw env values flow through for downstream wiring.
    boot = resolve_boot_persistence(
        persistence=ov.persistence,
        artifact_storage=ov.artifact_storage,
    )

    # Build every persistence-independent service, compose + populate each
    # feature's state slice (via ``run_construction_wiring``), and return the
    # collaborators the composition root threads into route assembly, the
    # lifespan hooks, and the Litestar build.
    result = build_construction_services(
        effective_config=effective_config,
        api_config=api_config,
        overrides=ov,
        boot=boot,
    )
    app_state = result.app_state

    # Route registration is discovery-based: collect every feature manifest's
    # controllers (api-mounted vs root-mounted) + websocket handlers, evaluating
    # each ControllerRegistration predicate against the constructed AppState so
    # a disabled or unwired subsystem's routes are not registered at all (404).
    api_handlers, root_handlers = collect_route_handlers(app_state)
    api_router = Router(
        path=api_config.api_prefix,
        route_handlers=api_handlers,
        guards=[require_password_changed],
    )

    startup, shutdown = assemble_lifespan_hooks(
        app_state,
        persistence=boot.persistence,
        message_bus=result.message_bus,
        bridge=result.bridge,
        settings_dispatcher=result.settings_dispatcher,
        task_engine=result.task_engine,
        meeting_scheduler=result.meeting_scheduler,
        backup_service=result.backup_service,
        approval_timeout_scheduler=result.approval_timeout_scheduler,
        should_auto_wire_settings=result.should_auto_wire_settings,
        effective_config=effective_config,
        connection_catalog=result.connection_catalog,
        provider_registry=result.provider_registry,
        cost_tracker=result.cost_tracker,
        approval_store=result.approval_store,
        performance_tracker=result.performance_tracker,
        notification_dispatcher=result.notification_dispatcher,
    )

    if _skip_lifecycle_shutdown:
        shutdown = []

    return build_litestar(
        app_state,
        api_config=api_config,
        api_router=api_router,
        root_handlers=root_handlers,
        middleware=result.middleware,
        plugins=result.plugins,
        startup=startup,
        shutdown=shutdown,
        skip_lifecycle_shutdown=_skip_lifecycle_shutdown,
    )
