"""Extracted helper functions for system prompt construction.

Pure data-building helpers used by :mod:`synthorg.engine.prompt` to assemble
template context, metadata dicts, and section tracking.  Separated to keep
``prompt.py`` under the 800-line limit.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import AutonomyDetailLevel, PersonalityMode
from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    AUTONOMY_MINIMAL,
    AUTONOMY_SUMMARY,
)
from synthorg.engine.token_estimation import DefaultTokenEstimator, PromptTokenEstimator
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.prompt import PROMPT_PERSONALITY_TRIMMED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.core.task import Task
    from synthorg.engine.prompt_profiles import PromptProfile
    from synthorg.providers.models import ToolDefinition
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)


# ── Personality trim metadata ─────────────────────────────────


class PersonalityTrimInfo(BaseModel):
    """Metadata about personality section trimming.

    Populated when the personality section exceeded the profile's
    ``max_personality_tokens`` and was progressively trimmed.
    Tier 3 (minimal fallback) is best-effort and may still exceed
    the budget if ``communication_style`` alone is too long.

    Attributes:
        before_tokens: Estimated tokens before trimming.
        after_tokens: Estimated tokens after trimming.
        max_tokens: The budget that was enforced.
        trim_tier: Highest trimming tier applied (1=drop enums,
            2=truncate description, 3=minimal fallback).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    before_tokens: int = Field(ge=0, description="Tokens before trimming")
    after_tokens: int = Field(ge=0, description="Tokens after trimming")
    max_tokens: int = Field(gt=0, description="Budget that was enforced")
    trim_tier: int = Field(
        ge=1,
        le=3,
        description="Highest trim tier applied (1-3)",
    )

    @model_validator(mode="after")
    def _check_cross_field_invariants(self) -> Self:
        if self.before_tokens <= self.max_tokens:
            msg = "before_tokens must exceed max_tokens (trimming was not needed)"
            raise ValueError(msg)
        if self.after_tokens > self.before_tokens:
            msg = "after_tokens must not exceed before_tokens"
            raise ValueError(msg)
        if self.trim_tier in {1, 2} and self.after_tokens > self.max_tokens:
            msg = (
                "after_tokens must not exceed max_tokens for "
                f"trim_tier {self.trim_tier} (only tier 3 is best-effort)"
            )
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def budget_met(self) -> bool:
        """Whether trimming brought the section within budget."""
        return self.after_tokens <= self.max_tokens


_AUTONOMY_LOOKUP: MappingProxyType[
    AutonomyDetailLevel,
    MappingProxyType[SeniorityLevel, str],
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
SECTION_PERSONALITY: Final[str] = "personality"
SECTION_SKILLS: Final[str] = "skills"
SECTION_AUTHORITY: Final[str] = "authority"
SECTION_ORG_POLICIES: Final[str] = "org_policies"
SECTION_AUTONOMY: Final[str] = "autonomy"
SECTION_TASK: Final[str] = "task"
SECTION_COMPANY: Final[str] = "company"
SECTION_TOOLS: Final[str] = "tools"
SECTION_CONTEXT_BUDGET: Final[str] = "context_budget"
SECTION_STRATEGY: Final[str] = "strategy"

# Sections trimmed when over token budget, least critical first.
# Strategy is trimmed before company because it is additive context.
# Tools section was removed from the default template per D22
# (non-inferable principle), but custom templates may still render tools.
TRIMMABLE_SECTIONS: Final[tuple[str, ...]] = (
    SECTION_STRATEGY,
    SECTION_COMPANY,
    SECTION_TASK,
    SECTION_ORG_POLICIES,
)


def _resolve_profile_flags(
    profile: PromptProfile | None,
) -> tuple[PersonalityMode, AutonomyDetailLevel, bool, bool]:
    """Extract rendering flags from profile, falling back to full defaults.

    Returns:
        ``(personality_mode, autonomy_detail, include_org_policies,
        simplify_criteria)``.
    """
    # Deferred import to avoid circular dependency at module level.
    from synthorg.engine.prompt_profiles import (  # noqa: PLC0415
        get_prompt_profile,
    )

    effective = profile if profile is not None else get_prompt_profile(None)
    return (
        effective.personality_mode,
        effective.autonomy_detail_level,
        effective.include_org_policies,
        effective.simplify_acceptance_criteria,
    )


def _estimate_personality_tokens(
    ctx: dict[str, Any],
    personality_mode: PersonalityMode,
    estimator: PromptTokenEstimator,
) -> int:
    """Estimate token count of the personality section as the template renders it.

    Assembles the text that the Jinja2 template would produce for the
    given *personality_mode*, including the section heading and inline
    markdown formatting (bold labels, list prefixes), and runs it
    through the estimator.

    Args:
        ctx: Template context dict with personality fields populated.
        personality_mode: Which rendering mode to estimate for.
        estimator: Token estimator instance.

    Returns:
        Estimated token count.
    """
    parts: list[str] = ["## Personality"]
    desc = ctx.get("personality_description", "")
    style = ctx.get("communication_style", "")

    if personality_mode == "full":
        if desc:
            parts.append(desc)
        parts.append(f"- **Communication style**: {style}")
        parts.append(f"- **Verbosity**: {ctx.get('verbosity', '')}")
        parts.append(f"- **Risk tolerance**: {ctx.get('risk_tolerance', '')}")
        parts.append(f"- **Creativity**: {ctx.get('creativity', '')}")
        parts.append(f"- **Decision-making**: {ctx.get('decision_making', '')}")
        parts.append(
            f"- **Collaboration preference**: {ctx.get('collaboration', '')}",
        )
        parts.append(f"- **Conflict approach**: {ctx.get('conflict_approach', '')}")
        traits = ctx.get("personality_traits", ())
        if traits:
            parts.append(f"- **Traits**: {', '.join(traits)}")
    elif personality_mode == "condensed":
        if desc:
            parts.append(desc)
        parts.append(f"- **Style**: {style}")
        traits = ctx.get("personality_traits", ())
        if traits:
            parts.append(f"- **Traits**: {', '.join(traits)}")
    else:
        # minimal
        parts.append(f"- **Style**: {style}")

    text = "\n".join(parts)
    return estimator.estimate_tokens(text)


def _truncate_description(description: str, max_chars: int) -> str:
    """Truncate a description to fit within a character limit.

    Truncates at the last word boundary before *max_chars*, appending
    ``"..."`` as a suffix.  Returns an empty string when *description*
    is empty or when *max_chars* is too small to hold at least one
    character plus the suffix.  Returns *description* unchanged when
    it already fits within *max_chars*.

    Args:
        description: Original description text.
        max_chars: Maximum character count for the result.

    Returns:
        Original, truncated, or empty string.
    """
    if not description:
        return ""
    suffix = "..."
    # Need at least room for one character + suffix.
    if max_chars < len(suffix) + 1:
        return ""
    if len(description) <= max_chars:
        return description

    budget = max_chars - len(suffix)
    truncated = description[:budget]
    # Find last space to avoid splitting mid-word.
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip() + suffix


def _try_condensed(
    ctx: dict[str, Any],
    max_tokens: int,
    estimator: PromptTokenEstimator,
) -> int | None:
    """Tier 1: drop enums by switching to condensed mode.

    Mutates ``ctx["personality_mode"]`` to ``"condensed"`` regardless
    of whether the budget is met.  Subsequent tiers build on this
    side-effect.

    Returns:
        The new token count when the condensed-mode estimate fits
        the budget; ``None`` when it still exceeds.
    """
    ctx["personality_mode"] = "condensed"
    tokens = _estimate_personality_tokens(ctx, "condensed", estimator)
    return tokens if tokens <= max_tokens else None


def _try_truncate_description(
    ctx: dict[str, Any],
    mode: PersonalityMode,
    max_tokens: int,
    estimator: PromptTokenEstimator,
) -> int | None:
    """Tier 2: truncate description to fit remaining budget.

    Mutates ``ctx["personality_description"]`` to a truncated (or
    empty) value regardless of whether the budget is met.  Subsequent
    tiers may overwrite the description further.

    Returns:
        The new token count when the truncated description fits the
        budget; ``None`` when even the truncated form exceeds.
    """
    saved_desc = ctx["personality_description"]
    ctx["personality_description"] = ""
    tokens_without = _estimate_personality_tokens(ctx, mode, estimator)
    remaining = max_tokens - tokens_without
    if remaining > 0:
        max_chars = remaining * 4  # Inverse of char/4 heuristic.
        ctx["personality_description"] = _truncate_description(
            saved_desc,
            max_chars,
        )
    else:
        ctx["personality_description"] = ""
    tokens = _estimate_personality_tokens(ctx, mode, estimator)
    return tokens if tokens <= max_tokens else None


def _trim_personality(
    ctx: dict[str, Any],
    profile: PromptProfile,
    estimator: PromptTokenEstimator | None = None,
) -> PersonalityTrimInfo | None:
    """Progressively trim personality fields to fit the token budget.

    Applies up to three tiers until the personality section fits
    within ``profile.max_personality_tokens``:

    1. Drop behavioral enum fields (override mode to ``"condensed"``).
       Only applied when starting mode is ``"full"``.
    2. Truncate ``personality_description`` to fit remaining budget.
    3. Fall back to ``"minimal"`` (communication_style only).

    Args:
        ctx: Mutable template context dict.  Modified in place.
        profile: Prompt profile with ``max_personality_tokens`` limit.
        estimator: Token estimator.  Defaults to
            :class:`DefaultTokenEstimator` when ``None``.

    Returns:
        :class:`PersonalityTrimInfo` when trimming was applied, or
        ``None`` when the section was already within budget.
    """
    if estimator is None:
        estimator = DefaultTokenEstimator()
    max_tokens = profile.max_personality_tokens
    mode: PersonalityMode = ctx["personality_mode"]
    before = _estimate_personality_tokens(ctx, mode, estimator)

    if before <= max_tokens:
        return None

    # Tier 1: Drop enums (switch to condensed).
    if mode == "full":
        after = _try_condensed(ctx, max_tokens, estimator)
        if after is not None:
            return _make_trim_info(before, after, max_tokens, 1)
        mode = "condensed"

    # Tier 2: Truncate description.
    desc = ctx.get("personality_description", "")
    if desc and mode == "condensed":
        after = _try_truncate_description(
            ctx,
            mode,
            max_tokens,
            estimator,
        )
        if after is not None:
            return _make_trim_info(before, after, max_tokens, 2)

    # Tier 3: Fall back to minimal (communication_style only).
    ctx["personality_mode"] = "minimal"
    ctx["personality_description"] = ""
    after = _estimate_personality_tokens(ctx, "minimal", estimator)
    return _make_trim_info(before, after, max_tokens, 3)


def _make_trim_info(
    before: int,
    after: int,
    max_tokens: int,
    tier: int,
) -> PersonalityTrimInfo:
    """Create trim info and log at DEBUG level.

    The engine layer separately logs at INFO with agent context.
    Emits a WARNING when the budget was not met (tier 3 best-effort).

    Returns:
        A :class:`PersonalityTrimInfo` summarising before/after token
        counts and the trim tier that produced them.
    """
    logger.debug(
        PROMPT_PERSONALITY_TRIMMED,
        before_tokens=before,
        after_tokens=after,
        max_tokens=max_tokens,
        trim_tier=tier,
    )
    if after > max_tokens:
        logger.warning(
            PROMPT_PERSONALITY_TRIMMED,
            budget_met=False,
            after_tokens=after,
            max_tokens=max_tokens,
            trim_tier=tier,
            msg="personality trimming reached tier 3 but budget not met",
        )
    return PersonalityTrimInfo(
        before_tokens=before,
        after_tokens=after,
        max_tokens=max_tokens,
        trim_tier=tier,
    )


def build_core_context(  # noqa: PLR0913
    agent: AgentIdentity,
    role: Role | None,
    effective_autonomy: EffectiveAutonomy | None = None,
    profile: PromptProfile | None = None,
    *,
    trimming_enabled: bool = True,
    estimator: PromptTokenEstimator | None = None,
) -> tuple[dict[str, Any], PersonalityTrimInfo | None]:
    """Build core template variables from agent identity and profile.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        effective_autonomy: Resolved autonomy for the current run.
        profile: Prompt profile controlling verbosity.  ``None``
            defaults to full rendering.
        trimming_enabled: When ``True``, enforce
            ``profile.max_personality_tokens`` via progressive trimming.
        estimator: Token estimator for personality trimming.  Defaults
            to :class:`DefaultTokenEstimator` when ``None``.

    Returns:
        Tuple of (template context dict, personality trim info or None).
    """
    personality = agent.personality
    authority = agent.authority
    personality_mode, autonomy_detail, include_org_policies, simplify_criteria = (
        _resolve_profile_flags(profile)
    )
    autonomy_map = _AUTONOMY_LOOKUP[autonomy_detail]

    ctx: dict[str, Any] = {
        "agent_name": agent.name,
        "agent_role": agent.role,
        "agent_department": agent.department,
        "agent_level": agent.level.value,
        "role_description": role.description if role else "",
        "personality_description": personality.description,
        "communication_style": personality.communication_style,
        "risk_tolerance": personality.risk_tolerance.value,
        "creativity": personality.creativity.value,
        "verbosity": personality.verbosity.value,
        "decision_making": personality.decision_making.value,
        "collaboration": personality.collaboration.value,
        "conflict_approach": personality.conflict_approach.value,
        "personality_traits": personality.traits,
        "primary_skills": tuple(s.name for s in agent.skills.primary),
        "secondary_skills": tuple(s.name for s in agent.skills.secondary),
        "can_approve": authority.can_approve,
        "reports_to": authority.reports_to or "",
        "can_delegate_to": authority.can_delegate_to,
        "budget_limit": authority.budget_limit,
        "autonomy_instructions": autonomy_map[agent.level],
        # Profile-driven template flags.
        "personality_mode": personality_mode,
        "include_org_policies": include_org_policies,
        "simplify_acceptance_criteria": simplify_criteria,
    }

    ctx["effective_autonomy"] = _format_autonomy(effective_autonomy)

    trim_info: PersonalityTrimInfo | None = None
    if trimming_enabled and profile is not None:
        trim_info = _trim_personality(ctx, profile, estimator=estimator)

    return ctx, trim_info


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
        Dict with agent_id, name, role, department, and level.
    """
    return {
        "agent_id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "department": agent.department,
        "level": agent.level.value,
    }


def compute_sections(  # noqa: PLR0913
    *,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...] = (),
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    custom_template: bool = False,
    context_budget: str | None = None,
    profile: PromptProfile | None = None,
    has_strategy: bool = False,
) -> tuple[str, ...]:
    """Determine which sections are present in the rendered prompt.

    The default template omits the tools section per D22 (non-inferable
    principle).  Custom templates may still render tools, so the tools
    section is tracked when ``available_tools`` is non-empty and a custom
    template is in use.

    Args:
        task: Optional task context.
        available_tools: Tool definitions (tracked for custom templates).
        company: Optional company context.
        org_policies: Company-wide policy texts.
        custom_template: Whether a custom template is being used.
        context_budget: Formatted context budget indicator string.
        profile: Prompt profile controlling section inclusion.
        has_strategy: Whether strategic analysis sections are present.

    Returns:
        Tuple of section names that are included.
    """
    _, _, include_policies, _ = _resolve_profile_flags(profile)

    sections: list[str] = [
        SECTION_IDENTITY,
        SECTION_PERSONALITY,
        SECTION_SKILLS,
        SECTION_AUTHORITY,
    ]
    if org_policies and include_policies:
        sections.append(SECTION_ORG_POLICIES)
    # Autonomy follows org_policies in the template.
    sections.append(SECTION_AUTONOMY)
    if has_strategy:
        sections.append(SECTION_STRATEGY)
    if task is not None:
        sections.append(SECTION_TASK)
    if available_tools and custom_template:
        sections.append(SECTION_TOOLS)
    if company is not None:
        sections.append(SECTION_COMPANY)
    if context_budget:
        sections.append(SECTION_CONTEXT_BUDGET)
    return tuple(sections)
