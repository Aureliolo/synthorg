# module-kind: code
"""Builder for the shared system ``console`` :class:`AgentIdentity`.

The operator console acts as one fixed, stable, ELEVATED system identity
rather than any business agent. Its tool visibility is broad (``*``) by
design: the operator's cockpit sees the whole control-plane surface and
safety comes per-action from the SecOps gate, the non-waivable hard-deny
floor, and the admin/settings guardrails, not from a hand-authored allowlist.
The identity id is derived deterministically from the console name so every
audit event attributes to the same console across restarts.
"""

from typing import Final

from synthorg.core.agent import AgentIdentity, ModelConfig, ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.settings.model_ref import parse_model_ref

CONSOLE_IDENTITY_NAME: Final[str] = "Operator Console"
"""Canonical display name (and stable-id seed) for the console identity."""

_CONSOLE_ROLE: Final[str] = "platform operator console"
_CONSOLE_DEPARTMENT: Final[str] = "Operations"
# The console sees the entire internal MCP surface; per-action governance,
# not tool-visibility narrowing, is what bounds it (locked decision: broad
# exposure, gated per-action).
_CONSOLE_MCP_CAPABILITIES: Final[tuple[NotBlankStr, ...]] = (NotBlankStr("*"),)


def build_console_identity(
    *,
    model_ref: str,
    autonomy_level: AutonomyLevel,
    clock: Clock | None = None,
) -> AgentIdentity | None:
    """Build the console identity, or ``None`` when no model is bound.

    Args:
        model_ref: The ``operator_console_model`` reference (canonical
            ``{provider, model_id}`` JSON, or a bare model string). A
            provider-less or empty ref yields ``None`` (fail-closed): the
            console cannot dispatch without an explicit model.
        autonomy_level: The autonomy tier the console acts under.
        clock: Optional clock for the cosmetic ``hiring_date``; defaults to
            the system clock.

    Returns:
        The frozen console :class:`AgentIdentity`, or ``None`` when the
        model ref names no complete ``(provider, model_id)`` pair.
    """
    ref = parse_model_ref(model_ref)
    if not ref.is_bound:
        return None
    resolved_clock: Clock = clock or SystemClock()
    return AgentIdentity(
        id=stable_agent_id(CONSOLE_IDENTITY_NAME),
        name=NotBlankStr(CONSOLE_IDENTITY_NAME),
        role=NotBlankStr(_CONSOLE_ROLE),
        department=NotBlankStr(_CONSOLE_DEPARTMENT),
        model=ModelConfig(
            provider=NotBlankStr(ref.provider),
            model_id=NotBlankStr(ref.model_id),
        ),
        tools=ToolPermissions(
            access_level=ToolAccessLevel.ELEVATED,
            mcp_capabilities=_CONSOLE_MCP_CAPABILITIES,
        ),
        autonomy_level=autonomy_level,
        hiring_date=resolved_clock.now().date(),
    )


__all__ = ["CONSOLE_IDENTITY_NAME", "build_console_identity"]
