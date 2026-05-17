"""Notification domain models.

The backend defines coarse categories (``approval``, ``budget``,
etc.) for sink-level routing. The frontend refines these into
fine-grained subcategories (``approvals.pending``,
``budget.exhausted``, etc.) for UI routing in
``web/src/types/notifications.ts``. The ``NotificationSeverity``
enum is shared 1:1 between backend and frontend.
"""

import copy
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class NotificationCategory(StrEnum):
    """Backend coarse notification categories for sink-level routing.

    The frontend uses fine-grained subcategories (e.g.
    ``approvals.pending``, ``budget.exhausted``) in
    ``web/src/types/notifications.ts``. The two taxonomies share
    the severity enum 1:1 but categories are intentionally different
    granularities.
    """

    APPROVAL = "approval"
    BUDGET = "budget"
    SECURITY = "security"
    SYSTEM = "system"
    AGENT = "agent"
    HEALTH = "health"
    ESCALATION = "escalation"


class NotificationSeverity(StrEnum):
    """Notification severity levels.

    Shared with frontend ``NotificationItem.severity``.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Notification(BaseModel):
    """An operator notification event.

    Frozen Pydantic model delivered via registered sinks. The
    ``category`` and ``severity`` fields form the shared event
    taxonomy with the frontend notification system.

    Attributes:
        id: Unique notification identifier.
        category: Event category for filtering and routing.
        severity: Severity level.
        title: Human-readable summary (one line).
        body: Detailed notification body.
        source: Originating subsystem (e.g. ``"budget.enforcer"``).
        timestamp: When the event occurred (UTC).
        metadata: Arbitrary structured context for adapters.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique notification identifier",
    )
    category: NotificationCategory = Field(
        description="Event category for filtering and routing",
    )
    severity: NotificationSeverity = Field(
        description="Severity level",
    )
    title: NotBlankStr = Field(
        description="Human-readable summary (one line)",
    )
    body: str = Field(
        default="",
        description="Detailed notification body",
    )
    source: NotBlankStr = Field(
        description="Originating subsystem",
    )
    timestamp: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the event occurred (UTC)",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary structured context for adapters",
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Notification:
        """Snapshot metadata at construction to prevent caller mutation."""
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self
