# module-kind: adapter
"""Adapter bridging the house-style layer into prompt construction.

Encapsulates the ambient-provider read and section rendering so that
``prompt_render.build_template_context`` delegates to a single call, mirroring
``engine/strategy/adapter.py``.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.engine.output_style.house_style import build_house_style_section
from synthorg.engine.output_style.provider import (
    HouseStyleProvider,
    current_house_style_provider,
)
from synthorg.observability import get_logger
from synthorg.observability.events.output_style import OUTPUT_STYLE_PROMPT_INJECTED

logger = get_logger(__name__)


def should_inject_house_style(
    agent: AgentIdentity, *, provider: HouseStyleProvider | None = None
) -> bool:
    """Whether an agent's prompt gets a house-style section.

    Args:
        agent: The agent whose prompt is being built.
        provider: An explicit provider snapshot (resolved once per prompt build
            so this and :func:`inject_house_style_context` agree even if the
            ambient provider is hot-swapped mid-build); falls back to ambient.

    Returns:
        ``True`` when a provider is bound and yields at least one directive in
        scope for the agent.
    """
    resolved = provider if provider is not None else current_house_style_provider()
    if resolved is None:
        return False
    return bool(resolved.list_directives(role=agent.role, department=agent.department))


def inject_house_style_context(
    context: dict[str, object],
    agent: AgentIdentity,
    *,
    provider: HouseStyleProvider | None = None,
) -> None:
    """Inject the house-style section into the template context.

    Sets ``house_style`` to ``True`` and ``house_style_section`` to the rendered
    body when the agent has in-scope directives; otherwise sets ``house_style``
    to ``False`` and ``house_style_section`` to ``None``.

    Args:
        context: The mutable Jinja2 template context.
        agent: The agent whose prompt is being built.
        provider: An explicit provider (tests); falls back to the ambient one.
    """
    resolved = provider if provider is not None else current_house_style_provider()
    directives = (
        resolved.list_directives(role=agent.role, department=agent.department)
        if resolved is not None
        else ()
    )
    if not directives:
        context["house_style"] = False
        context["house_style_section"] = None
        return
    context["house_style"] = True
    context["house_style_section"] = build_house_style_section(directives)
    logger.debug(
        OUTPUT_STYLE_PROMPT_INJECTED,
        agent_role=agent.role,
        agent_department=agent.department,
        directive_count=len(directives),
    )


__all__ = ["inject_house_style_context", "should_inject_house_style"]
