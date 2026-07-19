# module-kind: code
"""Sanctioned-exemption resolution for the output-style policy.

An exemption is granted deterministically only when the agent's output context
matches an operator-authored sanctioned scope for the offending rule. An agent
never self-grants: an inline ``output-style-allow`` marker is parsed for the
audit trail (the agent *requested* an exemption) but does not itself grant one,
so a lazy or adversarial model cannot bypass a hard ban by emitting the marker.
"""

import re
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.output_style.models import (
    ALL_RULES,
    ExemptionScopeKind,
    OutputChannel,
    SanctionedExemption,
)

#: Inline marker an agent may emit to request an exemption (audit only).
_MARKER_RE = re.compile(
    r"output-style-allow:\s*(?P<rule>[A-Za-z0-9_*\-]+)\s*--\s*(?P<reason>\S.*?)\s*$",
    re.MULTILINE,
)


class OutputContext(BaseModel):
    """Context for evaluating one piece of agent output.

    Carries the boundary channel plus the fields a sanctioned exemption is
    keyed on. Every scope field is optional; a scope whose field is absent
    simply never matches.

    Attributes:
        channel: The output boundary being evaluated.
        file_path: Repo-relative path for a code-file / commit output.
        task_type: The producing task's type, for a task-type scope.
        project_id: The project the output belongs to.
        department: The producing agent's department.
        role: The producing agent's role.
        deliverable_tags: Tags attached to the deliverable.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    channel: OutputChannel
    file_path: str | None = None
    task_type: str | None = None
    project_id: str | None = None
    department: str | None = None
    role: str | None = None
    deliverable_tags: tuple[str, ...] = Field(default=())


class ExemptionRequest(BaseModel):
    """An inline exemption marker an agent emitted (audit only).

    Attributes:
        rule_id: The rule the agent asked to exempt (``"*"`` for all).
        reason: The agent-supplied justification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rule_id: str
    reason: str


def parse_exemption_markers(text: str) -> tuple[ExemptionRequest, ...]:
    """Extract inline ``output-style-allow`` markers for the audit trail.

    Args:
        text: The agent output to scan.

    Returns:
        Every marker found, in document order. Never grants an exemption.
    """
    return tuple(
        ExemptionRequest(rule_id=m.group("rule"), reason=m.group("reason"))
        for m in _MARKER_RE.finditer(text)
    )


def _context_value(scope_kind: ExemptionScopeKind, ctx: OutputContext) -> str | None:
    """Return the scalar context value a non-tag scope compares against.

    Returns:
        The context field for ``scope_kind``, or ``None`` when absent or when
        the dimension is tag-based (handled separately).
    """
    if scope_kind is ExemptionScopeKind.PATH:
        return None if ctx.file_path is None else ctx.file_path.replace("\\", "/")
    if scope_kind is ExemptionScopeKind.TASK_TYPE:
        return ctx.task_type
    if scope_kind is ExemptionScopeKind.PROJECT:
        return ctx.project_id
    if scope_kind is ExemptionScopeKind.DEPARTMENT:
        return ctx.department
    if scope_kind is ExemptionScopeKind.ROLE:
        return ctx.role
    return None


def _scope_matches(exemption: SanctionedExemption, ctx: OutputContext) -> bool:
    """Whether a sanctioned exemption's scope matches the output context.

    Path scopes match with forward-slash-normalised, case-sensitive globs;
    every other dimension matches case-insensitively.

    Returns:
        True when the exemption's scope covers ``ctx``.
    """
    if exemption.scope_kind is ExemptionScopeKind.DELIVERABLE_TAG:
        pattern = exemption.match.casefold()
        return any(fnmatchcase(tag.casefold(), pattern) for tag in ctx.deliverable_tags)
    value = _context_value(exemption.scope_kind, ctx)
    if value is None:
        return False
    if exemption.scope_kind is ExemptionScopeKind.PATH:
        return fnmatchcase(value, exemption.match.replace("\\", "/"))
    return fnmatchcase(value.casefold(), exemption.match.casefold())


class ExemptionResolver:
    """Resolves whether a matched rule is sanctioned-exempt in a context."""

    def __init__(self, exemptions: tuple[SanctionedExemption, ...]) -> None:
        """Store the merged (pack + operator) sanctioned exemptions.

        Args:
            exemptions: All sanctioned exemptions in effect.
        """
        self._exemptions = exemptions

    def resolve(self, rule_id: str, ctx: OutputContext) -> SanctionedExemption | None:
        """Return the first sanctioned exemption covering a rule in a context.

        Args:
            rule_id: The offending rule's id.
            ctx: The output context.

        Returns:
            The matching :class:`SanctionedExemption`, or ``None`` when the
            rule is not exempt here.
        """
        for exemption in self._exemptions:
            if exemption.rule_id not in (rule_id, ALL_RULES):
                continue
            if _scope_matches(exemption, ctx):
                return exemption
        return None


__all__ = [
    "ExemptionRequest",
    "ExemptionResolver",
    "OutputContext",
    "parse_exemption_markers",
]
