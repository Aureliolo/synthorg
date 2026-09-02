"""Extracted helper functions for system prompt construction.

Pure data-building helpers used by :mod:`synthorg.engine.prompt` to assemble
template context, metadata dicts, and section tracking.  Separated to keep
``prompt.py`` under the 800-line limit.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, get_args

from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.role import Role
from synthorg.core.types import AutonomyDetailLevel
from synthorg.engine.prompt_profiles import PromptProfile
from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    AUTONOMY_MINIMAL,
    AUTONOMY_SUMMARY,
)
from synthorg.observability import get_logger
from synthorg.providers.models import ToolDefinition

if TYPE_CHECKING:
    from synthorg.core.company import Company
    from synthorg.core.effective_autonomy import EffectiveAutonomy

logger = get_logger(__name__)


# Fallback autonomy mode when no effective autonomy was resolved for the
# run: SEMI keeps the agent working independently while still deferring
# consequential actions, matching the pre-mode default posture.
_DEFAULT_AUTONOMY_MODE: Final[AutonomyLevel] = AutonomyLevel.SEMI

_AUTONOMY_LOOKUP: MappingProxyType[
    AutonomyDetailLevel,
    MappingProxyType[AutonomyLevel, str],
] = MappingProxyType(
    {
        "full": AUTONOMY_INSTRUCTIONS,
        "summary": AUTONOMY_SUMMARY,
        "minimal": AUTONOMY_MINIMAL,
    },
)

_expected_detail_levels = set(get_args(AutonomyDetailLevel))
_missing_detail = _expected_detail_levels - set(_AUTONOMY_LOOKUP)
if _missing_detail:
    _msg_d = f"Missing autonomy lookup for detail levels: {sorted(_missing_detail)}"
    raise ValueError(_msg_d)

# ── Section names ────────────────────────────────────────────────

SECTION_IDENTITY: Final[str] = "identity"
SECTION_HOUSE_STYLE: Final[str] = "house_style"
SECTION_SKILLS: Final[str] = "skills"
SECTION_AUTHORITY: Final[str] = "authority"
SECTION_ORG_POLICIES: Final[str] = "org_policies"
SECTION_AUTONOMY: Final[str] = "autonomy"
SECTION_ASK_POLICY: Final[str] = "ask_policy"
SECTION_COMPANY: Final[str] = "company"
SECTION_TOOLS: Final[str] = "tools"
SECTION_CONTEXT_BUDGET: Final[str] = "context_budget"
SECTION_STRATEGY: Final[str] = "strategy"

# Sections trimmed when over token budget, least critical first, and ONLY
# sections the trimmer can drop from its inputs: an entry here with no
# matching branch spends a render pass and trims nothing. Strategy is trimmed
# before company because it is additive context. The tools section was
# removed from the default template per D22 (non-inferable principle), but
# custom templates may still render tools. The task brief is not a section of
# this prompt at all: it travels once, as the first user message, pinned
# against compaction. The ask policy is appended after rendering rather than
# rendered from the inputs, so it is not trimmable either.
TRIMMABLE_SECTIONS: Final[tuple[str, ...]] = (
    SECTION_STRATEGY,
    SECTION_COMPANY,
    SECTION_ORG_POLICIES,
)


def _resolve_profile_flags(
    profile: PromptProfile | None,
) -> tuple[AutonomyDetailLevel, bool]:
    """Extract rendering flags from profile, falling back to full defaults.

    Returns:
        ``(autonomy_detail, include_org_policies)``.
    """
    # Deferred import to avoid circular dependency at module level.
    from synthorg.engine.prompt_profiles import (  # noqa: PLC0415
        get_prompt_profile,
    )

    effective = profile if profile is not None else get_prompt_profile(None)
    return (
        effective.autonomy_detail_level,
        effective.include_org_policies,
    )


def build_core_context(
    agent: AgentIdentity,
    role: Role | None,
    effective_autonomy: EffectiveAutonomy | None = None,
    profile: PromptProfile | None = None,
) -> dict[str, object]:
    """Build core template variables from agent identity and profile.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        effective_autonomy: Resolved autonomy for the current run.
        profile: Prompt profile controlling verbosity.  ``None``
            defaults to full rendering.

    Returns:
        The template context dict.
    """
    authority = agent.authority
    autonomy_detail, include_org_policies = _resolve_profile_flags(profile)
    autonomy_map = _AUTONOMY_LOOKUP[autonomy_detail]
    autonomy_mode = (
        effective_autonomy.level
        if effective_autonomy is not None
        else _DEFAULT_AUTONOMY_MODE
    )

    ctx: dict[str, object] = {
        "agent_name": agent.name,
        "agent_role": agent.role,
        "agent_department": agent.department,
        "role_description": role.description if role else "",
        "primary_skills": tuple(s.name for s in agent.skills.primary),
        "secondary_skills": tuple(s.name for s in agent.skills.secondary),
        "can_approve": authority.can_approve,
        "reports_to": authority.reports_to or "",
        "can_delegate_to": authority.can_delegate_to,
        "budget_limit": authority.budget_limit,
        "autonomy_instructions": autonomy_map[autonomy_mode],
        # The resolved pair itself, so the ask-policy adapter keys its directive
        # off exactly what the Autonomy section rendered from rather than
        # re-deriving it and risking the two sections disagreeing.
        "autonomy_mode": autonomy_mode,
        "autonomy_detail_level": autonomy_detail,
        # Profile-driven template flags.
        "include_org_policies": include_org_policies,
    }

    ctx["effective_autonomy"] = _format_autonomy(effective_autonomy)

    return ctx


def _format_autonomy(
    effective_autonomy: EffectiveAutonomy | None,
) -> dict[str, object] | None:
    """Format effective autonomy for template context.

    Returns:
        A dict carrying the autonomy level, sorted approval-action
        lists, and the security-agent flag; ``None`` when no
        effective autonomy was supplied.
    """
    if effective_autonomy is None:
        return None
    return {
        "level": effective_autonomy.level.value,
        "auto_approve_actions": sorted(effective_autonomy.auto_approve_actions),
        "human_approval_actions": sorted(
            effective_autonomy.human_approval_actions,
        ),
        "security_agent": effective_autonomy.security_agent,
    }


def build_metadata(agent: AgentIdentity) -> dict[str, str]:
    """Build metadata dict from agent identity.

    Args:
        agent: The agent identity.

    Returns:
        Dict with agent_id, name, role, and department.
    """
    return {
        "agent_id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "department": agent.department,
    }


def compute_sections(
    *,
    available_tools: tuple[ToolDefinition, ...] = (),
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    custom_template: bool = False,
    context_budget: str | None = None,
    profile: PromptProfile | None = None,
    injected_sections: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Determine which sections are present in the rendered prompt.

    The default template omits the tools section per D22 (non-inferable
    principle).  Custom templates may still render tools, so the tools
    section is tracked when ``available_tools`` is non-empty and a custom
    template is in use.

    The task brief is NOT a section here. It is a pinned USER message with
    one owner (``format_task_instruction``); rendering it into the system
    prompt as well made it two, at 88-94% byte overlap, and made the copy
    the trimmer dropped indistinguishable from the copy it kept.

    Args:
        available_tools: Tool definitions (tracked for custom templates).
        company: Optional company context.
        org_policies: Company-wide policy texts.
        custom_template: Whether a custom template is being used.
        context_budget: Formatted context budget indicator string.
        profile: Prompt profile controlling section inclusion.
        injected_sections: Names of the provider-driven optional sections the
            build injected (house style, ask policy, strategy). One set rather
            than a flag per section, so a fourth injectable layer does not widen
            this signature again.

    Returns:
        Tuple of section names that are included.
    """
    _, include_policies = _resolve_profile_flags(profile)

    sections: list[str] = [SECTION_IDENTITY]
    if SECTION_HOUSE_STYLE in injected_sections:
        sections.append(SECTION_HOUSE_STYLE)
    sections.extend((SECTION_SKILLS, SECTION_AUTHORITY))
    if org_policies and include_policies:
        sections.append(SECTION_ORG_POLICIES)
    # Autonomy follows org_policies in the template.
    sections.append(SECTION_AUTONOMY)
    # The ask directive is the exception to the autonomy licence, so it renders
    # directly after it.
    if SECTION_ASK_POLICY in injected_sections:
        sections.append(SECTION_ASK_POLICY)
    if SECTION_STRATEGY in injected_sections:
        sections.append(SECTION_STRATEGY)
    if available_tools and custom_template:
        sections.append(SECTION_TOOLS)
    if company is not None:
        sections.append(SECTION_COMPANY)
    if context_budget:
        sections.append(SECTION_CONTEXT_BUDGET)
    return tuple(sections)
