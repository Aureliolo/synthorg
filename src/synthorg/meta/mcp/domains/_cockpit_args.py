"""Typed argument models for the MCP ``cockpit`` steering tools.

Covers the project-scoped ``steer`` / ``steer_supersede`` / ``steer_list`` tools.
The pre-existing read + intervene cockpit tools keep their JSON-Schema-only
registration; these three are the new steering surface and route through
``parse_typed`` at the invoker so blank ids and malformed payloads are rejected
at the MCP boundary rather than slipping into the service write path.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention import SupersedeMode
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.meta.mcp.domains._common_args import AdminGuardrailFields, _ArgsBase


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
