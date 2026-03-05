"""System prompt construction from agent identity and context.

Translates agent configuration (personality, skills, authority, role) into
contextually rich system prompts that shape agent behavior during LLM calls.

Example::

    from ai_company.engine.prompt import build_system_prompt

    prompt = build_system_prompt(agent=agent_identity)
    print(prompt.content)
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from jinja2 import TemplateError as Jinja2TemplateError
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field

from ai_company.engine.errors import PromptBuildError
from ai_company.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    DEFAULT_TEMPLATE,
    PROMPT_TEMPLATE_VERSION,
)
from ai_company.observability import get_logger
from ai_company.observability.events import (
    PROMPT_BUILD_BUDGET_EXCEEDED,
    PROMPT_BUILD_ERROR,
    PROMPT_BUILD_START,
    PROMPT_BUILD_SUCCESS,
    PROMPT_BUILD_TOKEN_TRIMMED,
    PROMPT_CUSTOM_TEMPLATE_FAILED,
    PROMPT_CUSTOM_TEMPLATE_LOADED,
)

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.company import Company
    from ai_company.core.role import Role
    from ai_company.core.task import Task
    from ai_company.providers.models import ToolDefinition

logger = get_logger(__name__)

# Sandboxed to prevent arbitrary code execution in user-provided custom templates.
# Thread-safe for parse/render (read-only ops); do NOT mutate at runtime.
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
        description="Agent identity metadata (treat as read-only)",
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
            Estimated token count (0 for text shorter than 4 characters).
        """
        return len(text) // 4


# ── Section names ────────────────────────────────────────────────

_SECTION_IDENTITY = "identity"
_SECTION_PERSONALITY = "personality"
_SECTION_SKILLS = "skills"
_SECTION_AUTHORITY = "authority"
_SECTION_AUTONOMY = "autonomy"
_SECTION_TASK = "task"
_SECTION_TOOLS = "tools"
_SECTION_COMPANY = "company"

# Sections trimmed when over token budget, least critical first.
_TRIMMABLE_SECTIONS = (_SECTION_COMPANY, _SECTION_TOOLS, _SECTION_TASK)


# ── Public API ───────────────────────────────────────────────────


def build_system_prompt(  # noqa: PLR0913
    *,
    agent: AgentIdentity,
    role: Role | None = None,
    task: Task | None = None,
    available_tools: tuple[ToolDefinition, ...] = (),
    company: Company | None = None,
    max_tokens: int | None = None,
    custom_template: str | None = None,
    token_estimator: PromptTokenEstimator | None = None,
) -> SystemPrompt:
    """Build a system prompt from agent identity and optional context.

    Renders the agent's personality, skills, authority, and autonomy into
    a structured system prompt. Optional context (task, tools, company)
    adds additional sections.

    When ``max_tokens`` is provided and the prompt exceeds it, optional
    sections are progressively trimmed in order: company context, tool
    descriptions, task details.

    Args:
        agent: The agent identity to build the prompt for.
        role: Optional role with description to include.
        task: Optional current task context.
        available_tools: Tool definitions to include in the prompt.
        company: Optional company context.
        max_tokens: Maximum token budget for the prompt. ``None`` means
            no limit.
        custom_template: Optional Jinja2 template string. When provided,
            overrides the default template.
        token_estimator: Custom token estimator. Defaults to
            :class:`DefaultTokenEstimator`.

    Returns:
        Frozen :class:`SystemPrompt` with rendered content and metadata.

    Raises:
        PromptBuildError: If prompt construction fails (invalid template,
            rendering error, or unexpected error).
    """
    if max_tokens is not None and max_tokens <= 0:
        msg = f"max_tokens must be > 0, got {max_tokens}"
        logger.error(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            max_tokens=max_tokens,
        )
        raise PromptBuildError(msg)

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
            max_tokens=max_tokens,
            estimator=estimator,
        )
    except PromptBuildError:
        raise  # Already logged by inner functions.
    except Exception as exc:
        logger.exception(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error=str(exc),
        )
        msg = f"Unexpected error building prompt for agent '{agent.name}': {exc}"
        raise PromptBuildError(msg) from exc

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
) -> dict[str, Any]:
    """Build the core (always-present) template variables from agent identity.

    Args:
        agent: Agent identity.
        role: Optional role with description.

    Returns:
        Dict of core template variables.
    """
    personality = agent.personality
    authority = agent.authority

    return {
        "agent_name": agent.name,
        "agent_role": agent.role,
        "agent_department": agent.department,
        "agent_level": agent.level.value,
        "role_description": role.description if role else "",
        "personality_description": personality.description,
        "communication_style": personality.communication_style,
        "risk_tolerance": personality.risk_tolerance.value,
        "creativity": personality.creativity.value,
        "personality_traits": personality.traits,
        "primary_skills": agent.skills.primary,
        "secondary_skills": agent.skills.secondary,
        "can_approve": authority.can_approve,
        "reports_to": authority.reports_to or "",
        "can_delegate_to": authority.can_delegate_to,
        "budget_limit": authority.budget_limit,
        "autonomy_instructions": AUTONOMY_INSTRUCTIONS[agent.level],
    }


def _build_template_context(
    *,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
) -> dict[str, Any]:
    """Assemble the full Jinja2 template context from agent and optional inputs.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.

    Returns:
        Dict of template variables.
    """
    context = _build_core_context(agent, role)

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
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
) -> tuple[str, ...]:
    """Determine which sections are present in the rendered prompt.

    Args:
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.

    Returns:
        Tuple of section names that are included.
    """
    sections: list[str] = [
        _SECTION_IDENTITY,
        _SECTION_PERSONALITY,
        _SECTION_SKILLS,
        _SECTION_AUTHORITY,
        _SECTION_AUTONOMY,
    ]
    if task is not None:
        sections.append(_SECTION_TASK)
    if available_tools:
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
    max_tokens: int,
    estimator: PromptTokenEstimator,
) -> tuple[Task | None, tuple[ToolDefinition, ...], Company | None]:
    """Progressively remove optional sections until under token budget.

    Returns the (possibly cleared) task, available_tools, and company after
    trimming. Logs warnings for trimmed sections and budget-exceeded state.

    Args:
        template_str: Jinja2 template text.
        agent: Agent identity.
        role: Optional role.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.
        max_tokens: Token budget.
        estimator: Token estimator.

    Returns:
        Tuple of (task, available_tools, company) after trimming.
    """
    trimmed_sections: list[str] = []

    for section in _TRIMMABLE_SECTIONS:
        _, estimated = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            company,
            estimator,
        )
        if estimated <= max_tokens:
            break

        if section == _SECTION_COMPANY and company is not None:
            company = None
        elif section == _SECTION_TOOLS and available_tools:
            available_tools = ()
        elif section == _SECTION_TASK and task is not None:
            task = None
        else:
            continue

        trimmed_sections.append(section)

    _, estimated = _render_and_estimate(
        template_str,
        agent,
        role,
        task,
        available_tools,
        company,
        estimator,
    )

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

    return task, available_tools, company


def _render_with_trimming(  # noqa: PLR0913
    *,
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    max_tokens: int | None,
    estimator: PromptTokenEstimator,
) -> SystemPrompt:
    """Render the prompt, trimming optional sections if over token budget.

    Progressively removes optional sections (company, tools, task) until
    the prompt fits within ``max_tokens``, or all optional sections are
    exhausted.

    Args:
        template_str: Jinja2 template text.
        agent: Agent identity.
        role: Optional role.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.
        max_tokens: Token budget (``None`` = unlimited).
        estimator: Token estimator.

    Returns:
        Rendered :class:`SystemPrompt`.
    """
    content, estimated = _render_and_estimate(
        template_str,
        agent,
        role,
        task,
        available_tools,
        company,
        estimator,
    )

    if max_tokens is not None and estimated > max_tokens:
        task, available_tools, company = _trim_sections(
            template_str=template_str,
            agent=agent,
            role=role,
            task=task,
            available_tools=available_tools,
            company=company,
            max_tokens=max_tokens,
            estimator=estimator,
        )
        content, estimated = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            company,
            estimator,
        )

    sections = _compute_sections(
        task=task,
        available_tools=available_tools,
        company=company,
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
    estimator: PromptTokenEstimator,
) -> tuple[str, int]:
    """Render the template and estimate its token count.

    Args:
        template_str: Jinja2 template text.
        agent: Agent identity.
        role: Optional role.
        task: Optional task context.
        available_tools: Tool definitions.
        company: Optional company context.
        estimator: Token estimator.

    Returns:
        Tuple of (rendered content, estimated token count).
    """
    context = _build_template_context(
        agent=agent,
        role=role,
        task=task,
        available_tools=available_tools,
        company=company,
    )
    content = _render_template(template_str, context)
    return content, estimator.estimate_tokens(content)
