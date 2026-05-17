"""Telemetry reporter protocol and event model."""

from datetime import datetime  # noqa: TC003 -- Pydantic needs at runtime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.telemetry.config import DEFAULT_ENVIRONMENT, MAX_STRING_LENGTH


class TelemetryEvent(BaseModel):
    """A single telemetry event to report.

    Events carry only aggregate, anonymized data.  The
    ``PrivacyScrubber`` validates every event before it leaves
    the process.

    Attributes:
        event_type: Dot-separated event name (e.g.
            ``"deployment.heartbeat"``).
        deployment_id: Anonymous UUID identifying the deployment.
        synthorg_version: Installed SynthOrg version string.
        python_version: Python interpreter version.
        os_platform: Operating system platform identifier.
        environment: Deployment environment tag (``local-docker``,
            ``ci``, ``prod``, ...). Emitted as the OTel
            ``deployment.environment`` resource attribute so every
            span in Logfire can be filtered without joining on a
            startup event.
        timestamp: UTC timestamp of the event.
        properties: Event-specific key-value data.  Values are
            restricted to primitives (int, float, str, bool).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_type: NotBlankStr = Field(
        description="Dot-separated event name",
    )
    deployment_id: NotBlankStr = Field(
        description="Anonymous UUID for this deployment",
    )
    synthorg_version: NotBlankStr = Field(
        description="Installed SynthOrg version",
    )
    python_version: NotBlankStr = Field(
        description="Python interpreter version",
    )
    os_platform: NotBlankStr = Field(
        description="OS platform identifier",
    )
    environment: NotBlankStr = Field(
        default=DEFAULT_ENVIRONMENT,
        max_length=MAX_STRING_LENGTH,
        description="Deployment environment tag (local-docker / ci / prod / ...)",
    )
    timestamp: datetime = Field(
        description="UTC timestamp of the event",
    )
    properties: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description="Event-specific aggregate data",
    )


@runtime_checkable
class TelemetryReporter(Protocol):
    """Backend-agnostic interface for sending telemetry events.

    Implementations must be safe to call from async contexts.
    ``report`` may buffer events internally; ``flush`` forces
    delivery of buffered events; ``shutdown`` flushes and releases
    resources.
    """

    async def report(self, event: TelemetryEvent) -> None:
        """Send a single telemetry event."""
        ...

    async def flush(self) -> None:
        """Flush any buffered events to the backend."""
        ...

    async def shutdown(self) -> None:
        """Flush remaining events and release resources."""
        ...
