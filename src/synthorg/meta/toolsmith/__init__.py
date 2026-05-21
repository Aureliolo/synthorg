"""Self-extending toolkit (toolsmith) meta-subsystem.

The toolsmith lets the organisation extend its own MCP tool surface at
runtime: it detects a recurring capability gap, authors a sandboxed tool
following the existing ``ToolHandler`` + ``args_model`` contract, validates
the candidate against the golden benchmark before trusting it, and on pass
plus human approval registers it permanently so a later task can use it.

Tool creation is governed: it runs at the ``TOOL_CREATION`` proposal
altitude, behind the autonomy gate and the mandatory approval queue, exactly
like the self-improvement code-modification flow.
"""

from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    ToolBlueprint,
    ToolBlueprintState,
    ToolValidationResult,
)

__all__ = [
    "CapabilityGap",
    "ToolBlueprint",
    "ToolBlueprintState",
    "ToolValidationResult",
]
