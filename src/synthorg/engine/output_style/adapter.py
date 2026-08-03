# module-kind: adapter
"""Adapter bridging the house-style layer into prompt construction.

Encapsulates section rendering so that
``prompt_render.build_template_context`` delegates to a single call, mirroring
``engine/strategy/adapter.py``.

The provider is passed in, never read from the ambient global here. The whole
point of snapshotting it once per build is that a hot-swap cannot land between
the "does this prompt get a section" question and the section itself; a
fallback read inside either of these would put that race straight back, and
``None`` is a real answer (nothing bound at snapshot time), not a missing one.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.engine.output_style.house_style import build_house_style_section
from synthorg.engine.output_style.provider import HouseStyleProvider
from synthorg.observability import get_logger
from synthorg.observability.events.output_style import OUTPUT_STYLE_PROMPT_INJECTED

logger = get_logger(__name__)


def should_inject_house_style(
    agent: AgentIdentity, *, provider: HouseStyleProvider | None
) -> bool:
    """Whether an agent's prompt gets a house-style section.

    Args:
        agent: The agent whose prompt is being built.
        provider: The snapshotted provider, or ``None`` when none was bound.

    Returns:
        ``True`` when a provider is bound and yields at least one directive in
        scope for the agent.
    """
    if provider is None:
        return False
    return bool(provider.list_directives(role=agent.role, department=agent.department))


def inject_house_style_context(
    context: dict[str, object],
    agent: AgentIdentity,
    *,
    provider: HouseStyleProvider | None,
) -> None:
    """Inject the house-style section into the template context.

    Sets ``house_style`` to ``True`` and ``house_style_section`` to the rendered
    body when the agent has in-scope directives; otherwise sets ``house_style``
    to ``False`` and ``house_style_section`` to ``None``.

    Args:
        context: The mutable Jinja2 template context.
        agent: The agent whose prompt is being built.
        provider: The snapshotted provider, or ``None`` when none was bound.
    """
    directives = (
        provider.list_directives(role=agent.role, department=agent.department)
        if provider is not None
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
