"""System prompt construction from agent identity and context.

Translates agent configuration (personality, skills, authority, role) into
contextually rich system prompts that shape agent behavior during LLM calls.

**Non-inferable principle:** System prompts should contain only information
that agents cannot discover by reading the codebase or environment.  Tool
definitions, for example, are already delivered via the LLM provider's API
``tools`` parameter, so repeating them in the system prompt would increase
cost without benefit (per D22, arXiv:2602.11988).  The default template
therefore omits the ``Available Tools`` section.  Custom templates may still
reference ``{{ tools }}`` when explicitly needed.

Example::

    from synthorg.engine.prompt import build_system_prompt

    prompt = build_system_prompt(agent=agent_identity, task=task)
    prompt.content  # rendered system prompt string
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from jinja2 import TemplateError as Jinja2TemplateError
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.errors import PromptBuildError
from synthorg.engine.policy_validation import validate_policy_quality
from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    DEFAULT_TEMPLATE,
    PROMPT_TEMPLATE_VERSION,
)
from synthorg.observability import get_logger
from synthorg.observability.events.prompt import (
    PROMPT_BUILD_BUDGET_EXCEEDED,
    PROMPT_BUILD_ERROR,
    PROMPT_BUILD_START,
    PROMPT_BUILD_SUCCESS,
    PROMPT_BUILD_TOKEN_TRIMMED,
    PROMPT_CUSTOM_TEMPLATE_FAILED,
    PROMPT_CUSTOM_TEMPLATE_LOADED,
    PROMPT_POLICY_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.core.task import Task
    from synthorg.providers.models import ToolDefinition
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)

# Sandboxed to prevent arbitrary code execution in user-provided custom templates.
# Thread-safe for concurrent parse/render calls. Do NOT add filters, globals,
# or extensions after module initialization.
_SANDBOX_ENV = SandboxedEnvironment()


# ── Result model ─────────────────────────────────────────────────


class SystemPrompt(BaseModel):
    """Immutable result of system prompt construction.

    Attributes:
        content: Full rendered prompt text.
        template_version: Version of the template that produced this prompt.
        estimated_tokens: Token estimate of the prompt content.
        sections: Names of sections included in the prompt.
        metadata: Agent identity metadata (agent_id, name, role, department, level).
    """

    model_config = ConfigDict(frozen=True)

    content: str = Field(description="Full rendered prompt text")
    template_version: str = Field(
        description="Template version that produced this prompt",
    )
    estimated_tokens: int = Field(
        ge=0,
        description="Estimated token count of prompt content",
    )
    sections: tuple[str, ...] = Field(
        description="Names of sections included in the prompt",
    )
    metadata: dict[str, str] = Field(
        description="Agent identity metadata (string-only values; shallow-frozen)",
    )


# ── Token estimation protocol ────────────────────────────────────


@runtime_checkable
class PromptTokenEstimator(Protocol):
    """Runtime-checkable protocol for estimating token count from text.

    Implementors must define a single ``estimate_tokens`` method.
    """

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in the given text.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count.
        """
        ...


class DefaultTokenEstimator:
    """Heuristic token estimator using character-count approximation.

    Uses the common ``len(text) // 4`` heuristic. Suitable for rough
    estimates; swap in a tiktoken-based estimator for precision.
    """

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens as approximately 1 token per 4 characters.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count (minimum 0).
        """
        return len(text) // 4


# ── Section names ────────────────────────────────────────────────

_SECTION_IDENTITY = "identity"
_SECTION_PERSONALITY = "personality"
_SECTION_SKILLS = "skills"
_SECTION_AUTHORITY = "authority"
_SECTION_ORG_POLICIES = "org_policies"
_SECTION_AUTONOMY = "autonomy"
_SECTION_TASK = "task"
_SECTION_COMPANY = "company"
_SECTION_TOOLS = "tools"

# Sections trimmed when over token budget, least critical first.
# Tools section was removed from the default template per D22
# (non-inferable principle), but custom templates may still render tools.
_TRIMMABLE_SECTIONS = (
    _SECTION_COMPANY,
    _SECTION_TASK,
    _SECTION_ORG_POLICIES,
)


# ── Public API ───────────────────────────────────────────────────


def build_system_prompt(  # noqa: PLR0913
    *,
    agent: AgentIdentity,
    role: Role | None = None,
    task: Task | None = None,
    available_tools: tuple[ToolDefinition, ...] = (),
    company: Company | None = None,
    org_policies: tuple[str, ...] = (),
    max_tokens: int | None = None,
    custom_template: str | None = None,
    token_estimator: PromptTokenEstimator | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> SystemPrompt:
    """Build a system prompt from agent identity and optional context.

    When ``max_tokens`` is provided and the prompt exceeds it, optional
    sections are progressively trimmed (company, task, org_policies).

    Args:
        agent: Agent identity containing personality, skills, authority.
        role: Optional role with description and responsibilities.
        task: Optional task context injected into the prompt.
        available_tools: Tool definitions populated into template context
            for custom templates only; the default template omits tools
            per D22 (non-inferable principle).
        company: Opt-in. Non-inferable principle recommends omitting
            unless agents need org-level context they cannot discover.
        org_policies: Company-wide policy texts to inject into prompt.
        max_tokens: Token budget; sections are trimmed if exceeded.
        custom_template: Optional Jinja2 template string override.
        token_estimator: Custom token estimator (defaults to char/4).
        effective_autonomy: Resolved autonomy for the current run.

    Returns:
        Immutable :class:`SystemPrompt` with rendered content and metadata.

    Raises:
        PromptBuildError: If prompt construction fails.
    """
    _validate_max_tokens(agent, max_tokens)
    _validate_org_policies(agent, org_policies)

    # Advisory only — issues are logged but never block prompt construction.
    if org_policies:
        try:
            validate_policy_quality(org_policies)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                PROMPT_POLICY_VALIDATION_FAILED,
                agent_id=str(agent.id),
                exc_info=True,
            )

    logger.info(
        PROMPT_BUILD_START,
        agent_id=str(agent.id),
        agent_name=agent.name,
        has_task=task is not None,
        tool_count=len(available_tools),
        has_company=company is not None,
        has_custom_template=custom_template is not None,
    )

    try:
        estimator = token_estimator or DefaultTokenEstimator()
        template_str = _resolve_template(custom_template)

        result = _render_with_trimming(
            template_str=template_str,
            agent=agent,
            role=role,
            task=task,
            available_tools=available_tools,
            company=company,
            org_policies=org_policies,
            max_tokens=max_tokens,
            estimator=estimator,
            effective_autonomy=effective_autonomy,
        )
    except PromptBuildError:
        raise  # Already logged by inner functions.
    except MemoryError, RecursionError:
        logger.error(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error="non-recoverable error building prompt",
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.exception(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error=str(exc),
        )
        msg = f"Unexpected error building prompt for agent '{agent.name}': {exc}"
        raise PromptBuildError(msg) from exc

    return _log_and_return(agent, result)


def _validate_max_tokens(
    agent: AgentIdentity,
    max_tokens: int | None,
) -> None:
    """Raise ``PromptBuildError`` if ``max_tokens`` is non-positive."""
    if max_tokens is not None and max_tokens <= 0:
        msg = f"max_tokens must be > 0, got {max_tokens}"
        logger.error(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            max_tokens=max_tokens,
        )
        raise PromptBuildError(msg)


def _validate_org_policies(
    agent: AgentIdentity,
    org_policies: tuple[str, ...],
) -> None:
    """Raise ``PromptBuildError`` on blank or non-string policy entries.

    Args:
        agent: Agent identity for error context.
        org_policies: Policy texts to validate.

    Raises:
        PromptBuildError: If any policy entry is empty or whitespace-only.
    """
    for index, policy in enumerate(org_policies):
        if not isinstance(policy, str) or not policy.strip():
            msg = f"org_policies[{index}] must be a non-empty string"
            logger.error(
                PROMPT_BUILD_ERROR,
                agent_id=str(agent.id),
                error=msg,
            )
            raise PromptBuildError(msg)


def _log_and_return(
    agent: AgentIdentity,
    result: SystemPrompt,
) -> SystemPrompt:
    """Log prompt build success and return the result."""
    logger.info(
        PROMPT_BUILD_SUCCESS,
        agent_id=str(agent.id),
        sections=result.sections,
        estimated_tokens=result.estimated_tokens,
        template_version=result.template_version,
    )
    return result


# ── Private helpers ──────────────────────────────────────────────


def _resolve_template(custom_template: str | None) -> str:
    """Resolve the template string to use for rendering.

    Args:
        custom_template: Optional user-provided template string.

    Returns:
        The template string to render.

    Raises:
        PromptBuildError: If custom template syntax is invalid.
    """
    if custom_template is None:
        return DEFAULT_TEMPLATE

    # Early-fail: validate syntax before building the template context.
    # from_string() in _render_template would also catch this, but failing
    # here produces a more specific "invalid syntax" error message.
    try:
        _SANDBOX_ENV.parse(custom_template)
    except TemplateSyntaxError as exc:
        logger.exception(
            PROMPT_CUSTOM_TEMPLATE_FAILED,
            error=str(exc),
        )
        msg = f"Custom template has invalid Jinja2 syntax: {exc}"
        raise PromptBuildError(msg) from exc

    logger.debug(PROMPT_CUSTOM_TEMPLATE_LOADED)
    return custom_template


def _build_core_context(
    agent: AgentIdentity,
    role: Role | None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> dict[str, Any]:
    """Build the core (always-present) template variables from agent identity.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        effective_autonomy: Resolved autonomy for the current run.

    Returns:
        Dict of core template variables.
    """
    personality = agent.personality
    authority = agent.authority

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
        "primary_skills": agent.skills.primary,
        "secondary_skills": agent.skills.secondary,
        "can_approve": authority.can_approve,
        "reports_to": authority.reports_to or "",
        "can_delegate_to": authority.can_delegate_to,
        "budget_limit": authority.budget_limit,
        "autonomy_instructions": AUTONOMY_INSTRUCTIONS[agent.level],
    }

    if effective_autonomy is not None:
        ctx["effective_autonomy"] = {
            "level": effective_autonomy.level.value,
            "auto_approve_actions": sorted(effective_autonomy.auto_approve_actions),
            "human_approval_actions": sorted(effective_autonomy.human_approval_actions),
            "security_agent": effective_autonomy.security_agent,
        }
    else:
        ctx["effective_autonomy"] = None

    return ctx


def _build_template_context(  # noqa: PLR0913
    *,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    effective_autonomy: EffectiveAutonomy | None = None,
) -> dict[str, Any]:
    """Assemble the full Jinja2 template context from agent and optional inputs.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.
        org_policies: Company-wide policy texts.
        effective_autonomy: Resolved autonomy for the current run.

    Returns:
        Dict of template variables.
    """
    context = _build_core_context(agent, role, effective_autonomy)

    context["org_policies"] = org_policies

    context["task"] = (
        {
            "title": task.title,
            "description": task.description,
            "acceptance_criteria": tuple(
                {"description": c.description} for c in task.acceptance_criteria
            ),
            "budget_limit": task.budget_limit,
            "deadline": task.deadline,
        }
        if task is not None
        else None
    )

    context["tools"] = (
        tuple({"name": t.name, "description": t.description} for t in available_tools)
        if available_tools
        else None
    )

    if company is not None:
        context["company"] = {"name": company.name}
        context["company_departments"] = tuple(d.name for d in company.departments)
    else:
        context["company"] = None
        context["company_departments"] = None

    return context


def _compute_sections(
    *,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...] = (),
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    custom_template: bool = False,
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

    Returns:
        Tuple of section names that are included.
    """
    sections: list[str] = [
        _SECTION_IDENTITY,
        _SECTION_PERSONALITY,
        _SECTION_SKILLS,
        _SECTION_AUTHORITY,
    ]
    if org_policies:
        sections.append(_SECTION_ORG_POLICIES)
    # Autonomy follows org_policies in the template.
    sections.append(_SECTION_AUTONOMY)
    if task is not None:
        sections.append(_SECTION_TASK)
    if available_tools and custom_template:
        sections.append(_SECTION_TOOLS)
    if company is not None:
        sections.append(_SECTION_COMPANY)
    return tuple(sections)


def _render_template(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the given context.

    Args:
        template_str: Jinja2 template text.
        context: Template variables.

    Returns:
        Rendered prompt text.

    Raises:
        PromptBuildError: If rendering fails.
    """
    try:
        template = _SANDBOX_ENV.from_string(template_str)
        return template.render(**context)
    except Jinja2TemplateError as exc:
        logger.exception(PROMPT_BUILD_ERROR, error=str(exc))
        msg = f"System prompt rendering failed: {exc}"
        raise PromptBuildError(msg) from exc


def _build_metadata(agent: AgentIdentity) -> dict[str, str]:
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


def _trim_sections(  # noqa: PLR0913
    *,
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    max_tokens: int,
    estimator: PromptTokenEstimator,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> tuple[
    str,
    int,
    Task | None,
    Company | None,
    tuple[str, ...],
]:
    """Progressively remove optional sections until under token budget.

    Returns ``(content, estimated, task, company, org_policies)``
    so the caller can reuse the final render.
    """
    trimmed_sections: list[str] = []

    for section in _TRIMMABLE_SECTIONS:
        content, estimated = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            company,
            org_policies,
            estimator,
            effective_autonomy=effective_autonomy,
        )
        if estimated <= max_tokens:
            break

        if section == _SECTION_COMPANY and company is not None:
            company = None
        elif section == _SECTION_ORG_POLICIES and org_policies:
            org_policies = ()
        elif section == _SECTION_TASK and task is not None:
            task = None
        else:
            continue

        trimmed_sections.append(section)
    else:
        # All sections exhausted — do a final render.
        content, estimated = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            company,
            org_policies,
            estimator,
            effective_autonomy=effective_autonomy,
        )

    _log_trim_results(agent, max_tokens, estimated, trimmed_sections)

    return content, estimated, task, company, org_policies


def _log_trim_results(
    agent: AgentIdentity,
    max_tokens: int,
    estimated: int,
    trimmed_sections: list[str],
) -> None:
    """Log warnings for trimmed sections and/or budget-exceeded state."""
    if trimmed_sections:
        logger.warning(
            PROMPT_BUILD_TOKEN_TRIMMED,
            agent_id=str(agent.id),
            max_tokens=max_tokens,
            estimated_tokens=estimated,
            trimmed_sections=trimmed_sections,
        )
    if estimated > max_tokens:
        logger.warning(
            PROMPT_BUILD_BUDGET_EXCEEDED,
            agent_id=str(agent.id),
            max_tokens=max_tokens,
            estimated_tokens=estimated,
        )


def _render_with_trimming(  # noqa: PLR0913
    *,
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    max_tokens: int | None,
    estimator: PromptTokenEstimator,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> SystemPrompt:
    """Render the prompt, trimming optional sections if over token budget."""
    content, estimated = _render_and_estimate(
        template_str,
        agent,
        role,
        task,
        available_tools,
        company,
        org_policies,
        estimator,
        effective_autonomy=effective_autonomy,
    )

    if max_tokens is not None and estimated > max_tokens:
        content, estimated, task, company, org_policies = _trim_sections(
            template_str=template_str,
            agent=agent,
            role=role,
            task=task,
            available_tools=available_tools,
            company=company,
            org_policies=org_policies,
            max_tokens=max_tokens,
            estimator=estimator,
            effective_autonomy=effective_autonomy,
        )

    return _build_prompt_result(
        content,
        estimated,
        task,
        available_tools,
        company,
        org_policies,
        agent,
        custom_template=template_str is not DEFAULT_TEMPLATE,
    )


def _build_prompt_result(  # noqa: PLR0913
    content: str,
    estimated: int,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    agent: AgentIdentity,
    *,
    custom_template: bool = False,
) -> SystemPrompt:
    """Assemble the final ``SystemPrompt`` from rendered content."""
    sections = _compute_sections(
        task=task,
        available_tools=available_tools,
        company=company,
        org_policies=org_policies,
        custom_template=custom_template,
    )
    return SystemPrompt(
        content=content,
        template_version=PROMPT_TEMPLATE_VERSION,
        estimated_tokens=estimated,
        sections=sections,
        metadata=_build_metadata(agent),
    )


def _render_and_estimate(  # noqa: PLR0913
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    estimator: PromptTokenEstimator,
    *,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> tuple[str, int]:
    """Render the template and estimate its token count.

    Args:
        template_str: Jinja2 template text.
        agent: Agent identity.
        role: Optional role.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.
        org_policies: Company-wide policy texts.
        estimator: Token estimator.
        effective_autonomy: Resolved autonomy for the current run.

    Returns:
        Tuple of (rendered content, estimated token count).
    """
    context = _build_template_context(
        agent=agent,
        role=role,
        task=task,
        available_tools=available_tools,
        company=company,
        org_policies=org_policies,
        effective_autonomy=effective_autonomy,
    )
    content = _render_template(template_str, context)
    return content, estimator.estimate_tokens(content)


def build_error_prompt(
    identity: AgentIdentity,
    agent_id: str,
    system_prompt: SystemPrompt | None,
) -> SystemPrompt:
    """Return the existing system prompt or a minimal error placeholder.

    Used by the engine when the execution pipeline fails and a
    ``SystemPrompt`` was never built (or was partially built).

    Args:
        identity: Agent identity for metadata.
        agent_id: String agent identifier.
        system_prompt: Previously built prompt, or ``None``.

    Returns:
        The existing prompt if available, else a minimal placeholder.
    """
    if system_prompt is not None:
        return system_prompt
    metadata = {**_build_metadata(identity), "agent_id": agent_id}
    return SystemPrompt(
        content="",
        template_version="error",
        estimated_tokens=0,
        sections=(),
        metadata=metadata,
    )


def format_task_instruction(task: Task) -> str:
    """Format a task into a user message for the initial conversation.

    Args:
        task: Task to format.

    Returns:
        Markdown-formatted task instruction string.
    """
    parts = [f"# Task: {task.title}", "", task.description]

    if task.acceptance_criteria:
        parts.append("")
        parts.append("## Acceptance Criteria")
        parts.extend(f"- {c.description}" for c in task.acceptance_criteria)

    if task.budget_limit > 0:
        parts.append("")
        parts.append(f"**Budget limit:** ${task.budget_limit:.2f} USD")

    if task.deadline:
        parts.append("")
        parts.append(f"**Deadline:** {task.deadline}")

    return "\n".join(parts)
