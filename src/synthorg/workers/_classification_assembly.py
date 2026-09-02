# module-kind: code
"""Boot assembly for the post-execution error-taxonomy classification.

Builds the error-taxonomy config + classification sinks fed by every
classified execution, and bridges the operator-tunable per-detector
timeout onto the config. Split out of ``engine_assembly`` so that
orchestrator stays under its module-size cap.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.coordination_config import ErrorTaxonomyConfig
    from synthorg.engine.classification.protocol import ClassificationSink

logger = get_logger(__name__)


def build_classification(
    app_state: AppState,
    *,
    detector_timeout_seconds: float | None,
) -> tuple[ErrorTaxonomyConfig | None, tuple[ClassificationSink, ...]]:
    """Build the error-taxonomy config + classification sinks.

    Off by default: when ``coordination.error_taxonomy.enabled`` is False the
    config is ``None`` and the sink tuple empty, so the post-execution pipeline
    skips classification entirely (no behaviour change). When enabled the shared
    taxonomy store (the signals aggregator's reader) plus the performance and
    notification sinks are fed by every classified execution, and the
    operator-tunable per-detector timeout is bridged onto the config.

    Args:
        app_state: Application state holding the collaborator slices.
        detector_timeout_seconds: Per-detector isolation window, or ``None``
            to keep the config default.

    Returns:
        A ``(error_taxonomy_config_or_none, sinks)`` pair.
    """
    from synthorg.engine.classification.sinks import (  # noqa: PLC0415
        NotificationDispatcherSink,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.notifications.state import NotificationsStateSlice  # noqa: PLC0415

    config = app_state.config.coordination.error_taxonomy
    if not config.enabled:
        logger.info(
            API_APP_STARTUP,
            service="classification",
            note="error-taxonomy classification disabled; post-exec skips it",
        )
        return None, ()
    sinks: list[ClassificationSink] = []
    # The taxonomy store plays a dual role: it is both a classification sink
    # (write side, appended here) and the signals aggregator's reader. The
    # reference is captured once at engine construction; the slice field is set
    # once in engine/_construction.py and never replaced, so this shared object
    # stays valid for the engine's lifetime (no dangling-reference window).
    store = app_state.slice(EngineStateSlice).error_taxonomy_store
    if store is not None:
        sinks.append(store)
    dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    if dispatcher is not None:
        sinks.append(NotificationDispatcherSink(dispatcher))

    resolved = config
    if detector_timeout_seconds is not None:
        resolved = config.model_copy(
            update={"detector_timeout_seconds": detector_timeout_seconds},
        )
    logger.info(
        API_APP_STARTUP,
        service="classification",
        note="error-taxonomy classification enabled",
        sink_count=len(sinks),
    )
    return resolved, tuple(sinks)
