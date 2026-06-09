"""Validation helpers, async-state injection, and rendering log helpers.

Extracted from :mod:`synthorg.engine.prompt` to keep the main module
focused on the public :func:`build_system_prompt` orchestration.
"""

from jinja2 import TemplateError as Jinja2TemplateError
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    format_cost,
)
from synthorg.communication.async_tasks.models import AsyncTaskStateChannel
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.errors import PromptBuildError
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.prompt_template import DEFAULT_TEMPLATE
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.prompt import (
    PROMPT_BUILD_BUDGET_EXCEEDED,
    PROMPT_BUILD_ERROR,
    PROMPT_BUILD_SUCCESS,
    PROMPT_BUILD_TOKEN_TRIMMED,
    PROMPT_CUSTOM_TEMPLATE_FAILED,
    PROMPT_CUSTOM_TEMPLATE_LOADED,
)

_SANDBOX_ENV = SandboxedEnvironment()

logger = get_logger(__name__)


def validate_max_tokens(
    agent: AgentIdentity,
    max_tokens: int | None,
) -> None:
    """Raise ``PromptBuildError`` if ``max_tokens`` is non-positive.

    Raises:
        PromptBuildError: When ``max_tokens`` is provided but not a
            positive integer.
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


def validate_org_policies(
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


def log_trim_results(
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


def format_task_instruction(
    task: Task,
    *,
    currency: CurrencyCode = DEFAULT_CURRENCY,
) -> str:
    """Format a task into a user message for the initial conversation.

    User-controllable fields (title, description, acceptance criteria,
    and the deadline string) are wrapped via ``wrap_untrusted()`` with
    the ``<task-data>`` fence so the system prompt can instruct the
    model that their content is untrusted input. The budget marker
    stays outside the fence because it is a
    numeric, non-string value -- strings are the only values the fence
    protects against breakout for. The deadline is ISO-8601 validated
    upstream but originates from an API payload, so it too lives
    inside the fence.

    Args:
        task: Task to format.
        currency: ISO 4217 currency code for budget display.

    Returns:
        Markdown-formatted task instruction string with untrusted
        fields fenced.
    """
    inner: list[str] = [f"Title: {task.title}", "", task.description]
    if task.acceptance_criteria:
        inner.append("")
        inner.append("Acceptance Criteria:")
        inner.extend(f"- {c.description}" for c in task.acceptance_criteria)

    parts = ["# Task", "", wrap_untrusted(TAG_TASK_DATA, "\n".join(inner))]

    if task.budget_limit > 0:
        parts.append("")
        parts.append(f"**Budget limit:** {format_cost(task.budget_limit, currency)}")

    if task.deadline:
        parts.append("")
        # Even though ``task.deadline`` passes strict
        # ``datetime.fromisoformat()`` validation upstream, it still
        # originates from an API request payload -- the coding
        # guideline is unambiguous that any string-typed
        # attacker-controllable value interpolated into an LLM
        # prompt must be wrapped. Budget limit stays unwrapped
        # because it is
        # a numeric type, not a string.
        parts.append(
            f"**Deadline:** {wrap_untrusted(TAG_TASK_DATA, str(task.deadline))}",
        )

    return "\n".join(parts)


def log_prompt_build_success(
    agent: AgentIdentity,
    *,
    sections: tuple[str, ...],
    estimated_tokens: int,
    template_version: str,
) -> None:
    """Emit the PROMPT_BUILD_SUCCESS info log line."""
    logger.info(
        PROMPT_BUILD_SUCCESS,
        agent_id=str(agent.id),
        sections=sections,
        estimated_tokens=estimated_tokens,
        template_version=template_version,
    )


def resolve_template(custom_template: str | None) -> str:
    """Resolve the template string to use for rendering.

    Returns:
        ``custom_template`` when supplied and syntactically valid;
        the default template otherwise.

    Raises:
        PromptBuildError: If custom template syntax is invalid.
    """
    if custom_template is None:
        return DEFAULT_TEMPLATE

    try:
        _SANDBOX_ENV.parse(custom_template)
    except TemplateSyntaxError as exc:
        # Syntax errors from an operator-supplied custom template
        # can echo fragments of the attacker-controlled input;
        # scrub + drop traceback.
        logger.warning(
            PROMPT_CUSTOM_TEMPLATE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Custom template has invalid Jinja2 syntax: {type(exc).__name__}"
        raise PromptBuildError(msg) from exc

    logger.debug(PROMPT_CUSTOM_TEMPLATE_LOADED)
    return custom_template


def render_template(template_str: str, context: dict[str, object]) -> str:
    """Render a Jinja2 template string with the given context.

    Returns:
        The rendered template output.

    Raises:
        PromptBuildError: If rendering fails.
    """
    try:
        template = _SANDBOX_ENV.from_string(template_str)
        return template.render(**context)
    except Jinja2TemplateError as exc:
        # Use the scrubbed pattern: a template-rendering error
        # may carry user-supplied template fragments in its message.
        logger.warning(
            PROMPT_BUILD_ERROR,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"System prompt rendering failed: {type(exc).__name__}"
        raise PromptBuildError(msg) from exc


def inject_async_task_section(
    *,
    content: str,
    state: AsyncTaskStateChannel,
    estimator: PromptTokenEstimator,
) -> tuple[str, int]:
    """Append an async task state section to a rendered prompt content.

    Returns:
        Tuple of ``(new_content, new_token_count)``.
    """
    lines = [
        "\n\n## Active Async Tasks\n",
        *(
            f"- **{r.task_id}** ({r.agent_name}): "
            f"{r.status.value} "
            f"(started {r.created_at.isoformat()}, "
            f"updated {r.updated_at.isoformat()})"
            for r in state.records
        ),
    ]
    section = "\n".join(lines)
    new_content = content + section
    return new_content, estimator.estimate_tokens(new_content)
