"""Boot wiring for the output-style policy.

Binds the process-global ambient output-policy service (used by every output
boundary) and the soft-layer house-style provider (used by the prompt build)
from the ``output_style`` settings at startup. Needs only the settings service:
the pack loads from disk. A settings change re-binds both via
``OutputStyleSettingsSubscriber`` with no restart.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_output_style_policy(app_state: AppState) -> None:
    """Build + bind the output-style policy service from settings at boot.

    A no-op when the settings service is not yet available. ``rebuild_and_bind``
    always binds a service (fail-closed to the in-code em-dash ban on any
    recoverable error), so a bad pack never breaks boot yet enforcement stays
    active at every boundary; only a critical error propagates and is re-raised.
    """
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        settings_service_of,
    )

    if app_state.slice(SettingsStateSlice).settings_service is None:
        return
    from synthorg.engine.output_style.wiring import (  # noqa: PLC0415
        rebuild_and_bind_output_style,
    )

    try:
        service = await rebuild_and_bind_output_style(settings_service_of(app_state))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="output_style_policy",
            note="output-style wiring unavailable; skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_APP_STARTUP,
        service="output_style_policy",
        note="wired",
        pack=service.config.pack,
        enabled=service.config.enabled,
    )


__all__ = ["wire_output_style_policy"]
