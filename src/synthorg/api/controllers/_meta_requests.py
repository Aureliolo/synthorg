"""Request bodies for the Chief of Staff meta endpoints."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class ChatRequest(BaseModel):
    """Request body for the Chief of Staff chat endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr = Field(
        max_length=2000,
        description="Free-text question for the Chief of Staff agent.",
    )
    proposal_id: UUID | None = Field(
        default=None,
        description="Improvement proposal the question is scoped to, if any.",
    )
    alert_id: UUID | None = Field(
        default=None,
        description="Proactive alert the question is scoped to, if any.",
    )


class ConversationalProposeRequest(BaseModel):
    """Request body for the Chief of Staff clarify-and-propose endpoint."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(
        max_length=2000,
        description="Human message for the clarify-and-propose turn.",
    )
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="Existing conversation to append to; None starts a new one.",
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Project the proposal should be scoped to, if any.",
    )
