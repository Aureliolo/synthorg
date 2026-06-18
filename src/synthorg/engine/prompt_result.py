"""Result model and assembly helpers for system prompt construction.

Holds the immutable :class:`SystemPrompt` result, the final-assembly
helper that composes it from rendered content, the async-task-section
appender, and the build-success logger. Kept free of any dependency on
the render engine so the render module can import from here.
"""

import copy
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.async_tasks.models import AsyncTaskStateChannel
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine._prompt_helpers import PersonalityTrimInfo
from synthorg.engine._prompt_helpers import build_metadata as _build_metadata
from synthorg.engine._prompt_helpers import compute_sections as _compute_sections
from synthorg.engine.prompt_profiles import PromptProfile
from synthorg.engine.prompt_template import PROMPT_TEMPLATE_VERSION
from synthorg.engine.prompt_validation import (
    inject_async_task_section,
    log_prompt_build_success,
)
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.providers.models import ToolDefinition

if TYPE_CHECKING:
    from synthorg.core.company import Company


class SystemPrompt(BaseModel):
    """Immutable result of system prompt construction.

    Attributes:
        content: Full rendered prompt text.
        template_version: Version of the template that produced this prompt.
        estimated_tokens: Token estimate of the prompt content.
        sections: Names of sections included in the prompt.
        metadata: Agent identity metadata (agent_id, name, role,
            department, level, and optionally profile_tier).
        personality_trim_info: Populated when personality section was
            trimmed to fit the profile's token budget.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    personality_trim_info: PersonalityTrimInfo | None = Field(
        default=None,
        description="Populated when personality section was trimmed",
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy metadata so the frozen model cannot be aliased.

        Returns:
            The instance with ``metadata`` deep-copied.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


def build_prompt_result(  # noqa: PLR0913
    content: str,
    estimated: int,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    agent: AgentIdentity,
    *,
    custom_template: bool = False,
    context_budget: str | None = None,
    profile: PromptProfile | None = None,
    personality_trim_info: PersonalityTrimInfo | None = None,
    strategy_config: StrategyConfig | None = None,
) -> SystemPrompt:
    """Assemble the final ``SystemPrompt`` from rendered content.

    Returns:
        The composed :class:`SystemPrompt` with sections, token
        estimate, template version, and optional personality-trim
        info populated.
    """
    from synthorg.engine.strategy.prompt_injection import (  # noqa: PLC0415
        should_inject_strategy,
    )

    sections = _compute_sections(
        task=task,
        available_tools=available_tools,
        company=company,
        org_policies=org_policies,
        custom_template=custom_template,
        context_budget=context_budget,
        profile=profile,
        has_strategy=should_inject_strategy(agent, strategy_config),
    )
    metadata = _build_metadata(agent)
    if profile is not None:
        metadata["profile_tier"] = profile.tier
    return SystemPrompt(
        content=content,
        template_version=PROMPT_TEMPLATE_VERSION,
        estimated_tokens=estimated,
        sections=sections,
        metadata=metadata,
        personality_trim_info=personality_trim_info,
    )


def append_async_task_section(
    prompt: SystemPrompt,
    state: AsyncTaskStateChannel,
    estimator: PromptTokenEstimator,
) -> SystemPrompt:
    """Append an async task state section to a rendered prompt.

    This section is appended after trimming so it is never trimmed away.
    Recomputes ``estimated_tokens`` to reflect the injected content.

    Returns:
        A copy of ``prompt`` with the appended section and refreshed
        ``estimated_tokens``.
    """
    new_content, new_tokens = inject_async_task_section(
        content=prompt.content,
        state=state,
        estimator=estimator,
    )
    return prompt.model_copy(
        update={
            "content": new_content,
            "estimated_tokens": new_tokens,
            "sections": (*prompt.sections, "async_tasks"),
        },
    )


def _directive_block(tags: tuple[str, ...]) -> str:
    """Return the appended ``## Untrusted Content`` block for *tags*.

    Returns:
        The block text (leading separator included), or ``""`` when
        *tags* is empty.
    """
    if not tags:
        return ""
    from synthorg.engine.prompt_safety import (  # noqa: PLC0415
        untrusted_content_directive,
    )

    directive = untrusted_content_directive(tags)
    return f"\n\n## Untrusted Content\n\n{directive}\n"


def untrusted_content_directive_token_cost(
    tags: tuple[str, ...],
    estimator: PromptTokenEstimator,
) -> int:
    """Estimate the token cost of appending the directive for *tags*.

    Callers reserve this from the trim budget before rendering so the
    final prompt (content + directive, appended after trimming) still
    respects ``max_tokens``.

    Returns:
        The estimated token count of the appended block, or ``0`` when
        *tags* is empty.
    """
    block = _directive_block(tags)
    return estimator.estimate_tokens(block) if block else 0


def append_untrusted_content_directive(
    prompt: SystemPrompt,
    tags: tuple[str, ...],
    estimator: PromptTokenEstimator,
) -> SystemPrompt:
    """Append the SEC-1 untrusted-content directive for *tags*.

    Appended after trimming (like the async-task section) so the
    directive that tells the model to treat fenced ``<tag>`` blocks as
    inert data is never trimmed away while the fenced content it governs
    survives. A no-op when *tags* is empty (no fenced blocks were
    rendered). Recomputes ``estimated_tokens`` to cover the addition.

    Returns:
        A copy of ``prompt`` with the directive appended (or ``prompt``
        unchanged when *tags* is empty).
    """
    block = _directive_block(tags)
    if not block:
        return prompt
    new_content = f"{prompt.content}{block}"
    return prompt.model_copy(
        update={
            "content": new_content,
            "estimated_tokens": estimator.estimate_tokens(new_content),
            "sections": (*prompt.sections, "untrusted_content_directive"),
        },
    )


def log_and_return(
    agent: AgentIdentity,
    result: SystemPrompt,
) -> SystemPrompt:
    """Log prompt build success and return the result.

    Returns:
        The same ``result`` echoed back to the caller.
    """
    log_prompt_build_success(
        agent,
        sections=result.sections,
        estimated_tokens=result.estimated_tokens,
        template_version=result.template_version,
    )
    return result
