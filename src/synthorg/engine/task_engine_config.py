"""Task engine configuration model."""

from pydantic import BaseModel, ConfigDict, Field


class TaskEngineConfig(BaseModel):
    """Configuration for the centralized task engine.

    Controls queue sizing, drain behaviour on shutdown, and whether
    state-change snapshots are published to the message bus.

    Attributes:
        max_queue_size: Maximum pending mutations before backpressure
            is applied.  ``0`` means unbounded.
        observer_queue_size: Maximum pending observer events before
            events are dropped.  ``0`` means unbounded.  Defaults to
            ``max_queue_size`` when not set explicitly.
        drain_timeout_seconds: Seconds to wait for pending mutations
            to drain during ``stop()``.
        publish_snapshots: Whether to publish ``TaskStateChanged``
            events to the message bus after each mutation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_queue_size: int = Field(
        default=1000,
        ge=0,
        description="Maximum pending mutations (0 = unbounded)",
    )
    observer_queue_size: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Maximum pending observer events (0 = unbounded). "
            "Defaults to max_queue_size when None."
        ),
    )
    drain_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=300,
        description="Seconds to wait for drain during stop()",
    )
    publish_snapshots: bool = Field(
        default=True,
        description="Publish TaskStateChanged to message bus",
    )

    @property
    def effective_observer_queue_size(self) -> int:
        """Resolved observer queue size (falls back to max_queue_size)."""
        if self.observer_queue_size is not None:
            return self.observer_queue_size
        return self.max_queue_size
