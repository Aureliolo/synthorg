"""Destructive external-MCP operation detector.

The shell/SQL ``DestructiveOpDetector`` scans string arguments for command
syntax (``rm -rf``, ``DROP TABLE``),
which a third-party MCP call never contains: its intent lives in the tool
NAME (``mcp_github_delete_repository``) or a structured dispatch argument
(``{"action": "delete_channel"}``), not embedded shell text. This rule
closes that blind spot: it tokenises the MCP tool name and its string
argument values and escalates any call whose operation reads as
destructive, so a deploy-replace / channel-delete / repo-purge on an
external server routes to a human instead of executing unreviewed.

It only fires for ``ToolCategory.MCP`` and only ever ESCALATEs (never
auto-denies): the human, not a regex, makes the final call on a
third-party operation. Escalation is the safe direction, so an
over-broad match costs a confirmation, never data.
"""

import re
from datetime import UTC, datetime
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_MCP_DESTRUCTIVE_OP_DETECTED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules._utils import walk_string_values

logger = get_logger(__name__)

_RULE_NAME: Final[str] = "mcp_destructive_op_detector"

# Verb tokens that read as destructive on an external service. Matched as
# whole snake/kebab/space/camelCase tokens (so ``delete`` fires on
# ``delete_repository`` and ``deleteRepository`` but not on ``deleted`` or
# ``undelete``).
_DESTRUCTIVE_VERBS: Final[frozenset[str]] = frozenset(
    {
        "replace",
        "delete",
        "destroy",
        "drop",
        "purge",
        "remove",
        "revoke",
        "wipe",
        "terminate",
        "teardown",
        "uninstall",
        "deprovision",
        "decommission",
        "overwrite",
        "truncate",
        "disable",
        "erase",
        "expire",
        "reset",
    },
)
# The subset that reads as mass / irreversible destruction: same ESCALATE
# outcome, but a CRITICAL tier so the reviewer sees the higher stakes.
_CRITICAL_VERBS: Final[frozenset[str]] = frozenset(
    {"purge", "wipe", "destroy", "decommission", "deprovision", "drop"},
)

# Splits on a separator run OR a camelCase boundary. The boundary arm must
# see the original casing, so the split runs BEFORE lowercasing: folding
# first would collapse ``deleteChannel`` into one unmatchable token.
_TOKEN_SPLIT: Final[re.Pattern[str]] = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|[^A-Za-z0-9]+"
)


def _destructive_tokens(value: str) -> set[str]:
    """Return the destructive verb tokens present in ``value``.

    Returns:
        The subset of :data:`_DESTRUCTIVE_VERBS` appearing as whole
        tokens in ``value`` (case-insensitive).
    """
    tokens = {token.lower() for token in _TOKEN_SPLIT.split(value) if token}
    return tokens & _DESTRUCTIVE_VERBS


class MCPDestructiveOpDetector:
    """Escalates destructive operations on external MCP servers."""

    @property
    def name(self) -> str:
        """Rule name."""
        return _RULE_NAME

    def evaluate(self, context: SecurityContext) -> SecurityVerdict | None:
        """Escalate a destructive MCP call, else abstain.

        Returns:
            An ESCALATE verdict when the MCP tool name or an argument
            reads as destructive, or ``None`` (for a non-MCP call or a
            benign MCP call).
        """
        if context.tool_category is not ToolCategory.MCP:
            return None
        found: set[str] = _destructive_tokens(context.tool_name)
        for value in walk_string_values(context.arguments):
            found |= _destructive_tokens(value)
        if not found:
            return None

        names = sorted(found)
        risk = (
            ApprovalRiskLevel.CRITICAL
            if found & _CRITICAL_VERBS
            else ApprovalRiskLevel.HIGH
        )
        logger.warning(
            SECURITY_MCP_DESTRUCTIVE_OP_DETECTED,
            tool_name=context.tool_name,
            findings=names,
            risk_level=risk.value,
        )
        return SecurityVerdict(
            verdict=SecurityVerdictType.ESCALATE,
            reason=(
                f"Destructive external MCP operation ({', '.join(names)}) "
                "requires approval"
            ),
            risk_level=risk,
            matched_rules=(_RULE_NAME,),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )


__all__ = ["MCPDestructiveOpDetector"]
