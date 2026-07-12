# module-kind: feature
"""HTTP payload models for the ``/requests`` lifecycle controller.

Kept beside the controller so the controller module stays within its
tier budget while the request/response DTOs remain co-located with the
endpoints that consume them.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.client.models import TaskRequirement
from synthorg.core.types import NotBlankStr


class CreateRequestPayload(BaseModel):
    """Request payload for submitting a new client request."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    client_id: NotBlankStr = Field(description="Requesting client id")
    requirement: TaskRequirement = Field(description="Task requirement")


class RejectionPayload(BaseModel):
    """Payload carrying a rejection reason."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reason: NotBlankStr = Field(description="Reason for rejection")


class ScopingPayload(BaseModel):
    """Payload carrying scoping notes and an optional refined requirement."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    notes: NotBlankStr = Field(description="Scoping notes from the reviewer")
    refined_title: NotBlankStr | None = Field(default=None)
    refined_description: NotBlankStr | None = Field(default=None)
    refined_acceptance_criteria: tuple[NotBlankStr, ...] | None = Field(
        default=None,
    )
