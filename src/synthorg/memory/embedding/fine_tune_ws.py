"""WebSocket progress publishing for the fine-tuning orchestrator.

Isolates the Litestar-channels publish concern (the channel protocol and
the best-effort emit) from the orchestrator's pipeline lifecycle.
"""

import json
from typing import Protocol

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.fine_tune_models import FineTuneRun
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_FINE_TUNE_WS_EMIT_FAILED

logger = get_logger(__name__)


class ChannelsPlugin(Protocol):
    """Protocol for WebSocket channel publishing."""

    def publish(self, data: str, *, channels: list[str]) -> None:
        """Publish data to the given channels."""
        ...


def publish_ws_event(
    channels_plugin: ChannelsPlugin | None,
    event_type: str,
    run: FineTuneRun,
) -> None:
    """Best-effort emit a WebSocket event for a fine-tuning run.

    No-op when no channels plugin is configured.  Publish failures are
    logged and swallowed (``MemoryError`` / ``RecursionError`` still
    propagate via ``reraise_critical``).

    Args:
        channels_plugin: WS plugin, or ``None`` to skip.
        event_type: The WS event type to publish.
        run: Run whose id/stage/progress is serialised.
    """
    if channels_plugin is None:
        return
    try:
        payload = json.dumps(
            {
                "event_type": event_type,
                "channel": "system",
                "payload": {
                    "run_id": run.id,
                    "stage": run.stage.value,
                    "progress": run.progress,
                },
            },
        )
        channels_plugin.publish(
            payload,
            channels=["system"],
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_FINE_TUNE_WS_EMIT_FAILED,
            event_type=event_type,
            run_id=run.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
