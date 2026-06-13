"""Typed argument models for every MCP ``cockpit`` tool.

Covers the read surface (``get_live_activity`` / ``get_flight_recorder_frames``
/ ``seek_flight_recorder`` / ``steer_list``), the task-intervention surface
(``intervene_pause`` / ``intervene_kill``) and the project-scoped steering
surface (``steer`` / ``steer_supersede``). Every tool routes its external
``arguments`` through ``parse_typed`` at the invoker so blank ids and malformed
payloads are rejected at the MCP boundary rather than slipping into the service
write path.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention import SupersedeMode
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)


class LiveActivityArgs(_ArgsBase):
    """Args for ``cockpit.get_live_activity`` (read, no parameters)."""


class FramesArgs(PaginationFields):
    """Args for ``cockpit.get_flight_recorder_frames`` (read)."""

    execution_id: NotBlankStr = Field(description="Execution run identifier")


class SeekArgs(_ArgsBase):
    """Args for ``cockpit.seek_flight_recorder`` (read)."""

    execution_id: NotBlankStr = Field(description="Execution run identifier")
    turn_index: int = Field(ge=1, description="1-based target turn index")


class InterveneArgs(AdminGuardrailFields):
    """Args for ``cockpit.intervene_pause`` / ``intervene_kill`` (admin).

    Both task-lifecycle interventions share the same surface: a single
    ``task_id`` plus the admin guardrail fields.
    """

    task_id: NotBlankStr = Field(description="Task to act on")


class SteerArgs(AdminGuardrailFields):
    """Args for ``cockpit.steer`` (admin)."""

    project_id: NotBlankStr = Field(description="Project the directive targets")
    kind: InterventionKind = Field(
        description="HINT (advisory) or REDIRECT (replan)",
    )
    text: NotBlankStr = Field(description="The operator directive text")
    narrow_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional task-id narrowing; empty means project-wide",
    )
    narrow_agent_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional agent-id narrowing; empty means every agent",
    )
    supersede_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tasks to treat as obsolete (EXPLICIT cancels, PROPOSE seeds)",
    )
    supersede_mode: SupersedeMode = Field(
        default=SupersedeMode.NONE,
        description="How obsolete tasks are handled",
    )


class SteerSupersedeArgs(AdminGuardrailFields):
    """Args for ``cockpit.steer_supersede`` (admin)."""

    project_id: NotBlankStr = Field(description="Project the directive targets")
    directive_id: NotBlankStr = Field(description="Directive to confirm for")
    task_ids: tuple[NotBlankStr, ...] = Field(
        description="Operator-confirmed obsolete tasks to cancel",
    )


class SteerListArgs(_ArgsBase):
    """Args for ``cockpit.steer_list`` (read)."""

    project_id: NotBlankStr = Field(description="Project the directive targets")
