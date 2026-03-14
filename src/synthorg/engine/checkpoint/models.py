"""Checkpoint and heartbeat models for crash recovery.

``Checkpoint`` persists a serialized ``AgentContext`` after each completed
turn so that execution can resume from the last checkpoint on crash.
``Heartbeat`` tracks liveness for stale-execution detection.
``CheckpointConfig`` controls checkpoint frequency and resume limits.
"""

import json
from datetime import UTC, datetime
from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001


class Checkpoint(BaseModel):
    """Serialized snapshot of an agent execution at a given turn.

    Attributes:
        id: Unique checkpoint identifier.
        execution_id: The execution run ID from ``AgentContext``.
        agent_id: Agent whose execution was checkpointed.
        task_id: Task the agent was working on.
        turn_number: Turn index at which this checkpoint was taken.
        context_json: JSON-serialized ``AgentContext``.
        created_at: Timestamp when the checkpoint was created.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique checkpoint identifier",
    )
    execution_id: NotBlankStr = Field(description="Execution run identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    turn_number: int = Field(ge=0, description="Turn index of this checkpoint")
    context_json: str = Field(description="JSON-serialized AgentContext")
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the checkpoint was created",
    )

    @model_validator(mode="after")
    def _validate_context_json(self) -> Self:
        """Validate that context_json is a valid JSON object."""
        try:
            parsed = json.loads(self.context_json)
        except (json.JSONDecodeError, TypeError) as exc:
            msg = f"context_json must be valid JSON: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(parsed, dict):
            msg = "context_json must be a JSON object, not a primitive or array"
            raise ValueError(msg)  # noqa: TRY004
        return self


class Heartbeat(BaseModel):
    """Liveness signal for a running agent execution.

    Attributes:
        execution_id: The execution run identifier (unique key).
        agent_id: Agent whose execution is being tracked.
        task_id: Task the agent was working on.
        last_heartbeat_at: Timestamp of the last heartbeat update.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: NotBlankStr = Field(description="Execution run identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    last_heartbeat_at: AwareDatetime = Field(
        description="Timestamp of the last heartbeat",
    )


class CheckpointConfig(BaseModel):
    """Configuration for checkpoint persistence and resume behavior.

    Attributes:
        persist_every_n_turns: Save a checkpoint every N turns.
        heartbeat_interval_seconds: Heartbeat update interval (reserved
            for future background heartbeat loop; not used by the
            per-turn callback).
        max_resume_attempts: Maximum number of resume attempts before
            falling back to fail-and-reassign.
    """

    model_config = ConfigDict(frozen=True)

    persist_every_n_turns: int = Field(
        default=1,
        gt=0,
        description="Save a checkpoint every N turns",
    )
    heartbeat_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Heartbeat update interval in seconds (reserved for "
            "future background heartbeat loop; not used by the "
            "per-turn callback)"
        ),
    )
    max_resume_attempts: int = Field(
        default=2,
        ge=0,
        description="Max resume attempts before fallback",
    )
