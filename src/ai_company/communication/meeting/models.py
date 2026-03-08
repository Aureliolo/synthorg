"""Meeting protocol domain models (DESIGN_SPEC Section 5.7)."""

from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from ai_company.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.core.enums import Priority
from ai_company.core.types import NotBlankStr, validate_unique_strings


class AgentResponse(BaseModel):
    """Result of a single agent invocation during a meeting.

    Attributes:
        agent_id: Identifier of the agent that responded.
        content: Text content of the response.
        input_tokens: Tokens consumed by the prompt.
        output_tokens: Tokens generated in the response.
        cost_usd: Estimated cost of the invocation.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Agent that responded")
    content: str = Field(description="Response content")
    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Prompt tokens consumed",
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Response tokens generated",
    )
    cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated invocation cost",
    )


class MeetingAgendaItem(BaseModel):
    """A single topic on the meeting agenda.

    Attributes:
        title: Short title of the agenda topic.
        description: Detailed description of the topic.
        presenter_id: Agent who presents this item (optional).
    """

    model_config = ConfigDict(frozen=True)

    title: NotBlankStr = Field(description="Agenda topic title")
    description: str = Field(
        default="",
        description="Detailed topic description",
    )
    presenter_id: NotBlankStr | None = Field(
        default=None,
        description="Agent who presents this item",
    )


class MeetingAgenda(BaseModel):
    """Full meeting agenda.

    Attributes:
        title: Meeting title.
        context: Background context for the meeting.
        items: Ordered agenda items.
    """

    model_config = ConfigDict(frozen=True)

    title: NotBlankStr = Field(description="Meeting title")
    context: str = Field(
        default="",
        description="Background context for the meeting",
    )
    items: tuple[MeetingAgendaItem, ...] = Field(
        default=(),
        description="Ordered agenda items",
    )


class MeetingContribution(BaseModel):
    """An agent's contribution during a meeting phase.

    Attributes:
        agent_id: Identifier of the contributing agent.
        content: Text content of the contribution.
        phase: Meeting phase during which this was contributed.
        turn_number: Turn number within the meeting.
        input_tokens: Prompt tokens consumed.
        output_tokens: Response tokens generated.
        timestamp: When the contribution was recorded.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Contributing agent")
    content: str = Field(description="Contribution content")
    phase: MeetingPhase = Field(description="Phase of contribution")
    turn_number: int = Field(ge=0, description="Turn number")
    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Prompt tokens consumed",
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Response tokens generated",
    )
    timestamp: AwareDatetime = Field(description="When recorded")


class ActionItem(BaseModel):
    """An action item extracted from meeting decisions.

    Attributes:
        description: What needs to be done.
        assignee_id: Agent responsible for the action.
        priority: Urgency of the action item.
    """

    model_config = ConfigDict(frozen=True)

    description: NotBlankStr = Field(description="What needs to be done")
    assignee_id: NotBlankStr | None = Field(
        default=None,
        description="Agent responsible for the action",
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Action item priority",
    )


class MeetingMinutes(BaseModel):
    """Complete output of a meeting protocol execution.

    Attributes:
        meeting_id: Unique meeting identifier.
        protocol_type: Protocol that produced these minutes.
        leader_id: Agent who led the meeting.
        participant_ids: Agents who participated.
        agenda: The meeting agenda.
        contributions: All agent contributions in order.
        summary: Final meeting summary text.
        decisions: Decisions made during the meeting.
        action_items: Extracted action items.
        conflicts_detected: Whether conflicts were detected.
        total_input_tokens: Total prompt tokens consumed.
        total_output_tokens: Total response tokens generated.
        started_at: When the meeting started.
        ended_at: When the meeting ended.
    """

    model_config = ConfigDict(frozen=True)

    meeting_id: NotBlankStr = Field(description="Unique meeting ID")
    protocol_type: MeetingProtocolType = Field(
        description="Protocol used",
    )
    leader_id: NotBlankStr = Field(description="Meeting leader")
    participant_ids: tuple[NotBlankStr, ...] = Field(
        description="Meeting participants",
    )
    agenda: MeetingAgenda = Field(description="Meeting agenda")
    contributions: tuple[MeetingContribution, ...] = Field(
        default=(),
        description="All contributions in order",
    )
    summary: str = Field(default="", description="Final summary")
    decisions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Decisions made",
    )
    action_items: tuple[ActionItem, ...] = Field(
        default=(),
        description="Extracted action items",
    )
    conflicts_detected: bool = Field(
        default=False,
        description="Whether conflicts were detected",
    )
    total_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Total prompt tokens",
    )
    total_output_tokens: int = Field(
        default=0,
        ge=0,
        description="Total response tokens",
    )
    started_at: AwareDatetime = Field(description="Meeting start time")
    ended_at: AwareDatetime = Field(description="Meeting end time")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.total_input_tokens + self.total_output_tokens

    @model_validator(mode="after")
    def _validate_timing(self) -> Self:
        """Ensure ended_at is not before started_at."""
        if self.ended_at < self.started_at:
            msg = "ended_at must not be before started_at"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_participants(self) -> Self:
        """Ensure participant IDs are unique and leader is not among them."""
        validate_unique_strings(self.participant_ids, "participant_ids")
        if self.leader_id in self.participant_ids:
            msg = "leader_id must not be in participant_ids"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_token_aggregates(self) -> Self:
        """Ensure aggregate token counts match sum of contributions.

        Skipped when no contributions are present (allows default
        zero totals).
        """
        if not self.contributions:
            if self.total_input_tokens != 0 or self.total_output_tokens != 0:
                msg = (
                    "total_input_tokens and total_output_tokens must "
                    "be 0 when contributions are empty"
                )
                raise ValueError(msg)
            return self
        sum_input = sum(c.input_tokens for c in self.contributions)
        sum_output = sum(c.output_tokens for c in self.contributions)
        if self.total_input_tokens != sum_input:
            msg = (
                f"total_input_tokens ({self.total_input_tokens}) "
                f"does not match sum of contributions "
                f"({sum_input})"
            )
            raise ValueError(msg)
        if self.total_output_tokens != sum_output:
            msg = (
                f"total_output_tokens ({self.total_output_tokens})"
                f" does not match sum of contributions "
                f"({sum_output})"
            )
            raise ValueError(msg)
        return self


class MeetingRecord(BaseModel):
    """Audit trail entry for a meeting execution.

    Attributes:
        meeting_id: Unique meeting identifier.
        meeting_type_name: Name of the meeting type from config.
        protocol_type: Protocol strategy used.
        status: Final status of the meeting.
        minutes: Complete minutes if meeting succeeded.
        error_message: Error description if meeting failed.
        token_budget: Token budget that was allocated.
    """

    model_config = ConfigDict(frozen=True)

    meeting_id: NotBlankStr = Field(description="Unique meeting ID")
    meeting_type_name: NotBlankStr = Field(
        description="Meeting type from config",
    )
    protocol_type: MeetingProtocolType = Field(
        description="Protocol strategy used",
    )
    status: MeetingStatus = Field(description="Final meeting status")
    minutes: MeetingMinutes | None = Field(
        default=None,
        description="Complete minutes on success",
    )
    error_message: NotBlankStr | None = Field(
        default=None,
        description="Error description on failure",
    )
    token_budget: int = Field(
        gt=0,
        description="Token budget allocated",
    )

    @model_validator(mode="after")
    def _validate_status_consistency(self) -> Self:
        """Enforce minutes/error field correlation with status."""
        if self.status == MeetingStatus.COMPLETED:
            if self.minutes is None:
                msg = "minutes are required when status is completed"
                raise ValueError(msg)
            if self.error_message is not None:
                msg = "error_message must be None when status is completed"
                raise ValueError(msg)
        if self.status in (
            MeetingStatus.FAILED,
            MeetingStatus.BUDGET_EXHAUSTED,
        ):
            if self.error_message is None:
                msg = (
                    "error_message is required when status is "
                    "failed or budget_exhausted"
                )
                raise ValueError(msg)
            if self.minutes is not None:
                msg = "minutes must be None when status is failed or budget_exhausted"
                raise ValueError(msg)
        return self
