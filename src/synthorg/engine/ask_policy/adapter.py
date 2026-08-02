# module-kind: adapter
"""Adapter bridging the ask policy into prompt construction.

Encapsulates section rendering so that
``prompt_render.build_template_context`` delegates to a single call, mirroring
``engine/output_style/adapter.py``.

The autonomy level and verbosity tier are read out of the template context that
``build_core_context`` already resolved, never re-derived here. Two independent
derivations of the same pair is how a prompt ends up telling an agent it is FULL
in one section and instructing a SUPERVISED agent in the next. The provider
arrives the same way, snapshotted by the caller, for the same reason.
"""

from typing import Final, cast

from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import AutonomyDetailLevel
from synthorg.engine.ask_policy.provider import AskPolicyProvider
from synthorg.engine.ask_policy.section import build_ask_policy_section
from synthorg.observability import get_logger
from synthorg.observability.events.ask_policy import ASK_POLICY_PROMPT_INJECTED

logger = get_logger(__name__)

#: Context keys ``build_core_context`` stashes the resolved autonomy pair under.
CONTEXT_AUTONOMY_MODE: Final[str] = "autonomy_mode"
CONTEXT_AUTONOMY_DETAIL: Final[str] = "autonomy_detail_level"


def should_inject_ask_policy(*, provider: AskPolicyProvider | None) -> bool:
    """Whether the prompt gets an ask-policy section.

    Unlike the house-style predicate this takes no agent: the standing directive
    is total over autonomy levels and tiers, so a bound and enabled provider
    always yields one for every agent. Only the operator additions are scoped,
    and an agent with none in scope still gets the standing directive.

    Args:
        provider: The snapshotted provider, or ``None`` when none was bound.

    Returns:
        ``True`` when a provider is bound and the subsystem is enabled.
    """
    return provider is not None and provider.enabled


def inject_ask_policy_context(
    context: dict[str, object],
    agent: AgentIdentity,
    *,
    provider: AskPolicyProvider | None,
) -> None:
    """Inject the ask-policy section into the template context.

    Sets ``ask_policy`` to ``True`` and ``ask_policy_section`` to the rendered
    body when the subsystem is on; otherwise sets ``ask_policy`` to ``False``
    and ``ask_policy_section`` to ``None``.

    Args:
        context: The mutable Jinja2 template context, already carrying the
            resolved autonomy level and verbosity tier.
        agent: The agent whose prompt is being built.
        provider: The snapshotted provider, or ``None`` when none was bound.
    """
    if provider is None or not provider.enabled:
        context["ask_policy"] = False
        context["ask_policy_section"] = None
        return
    autonomy = cast("AutonomyLevel", context[CONTEXT_AUTONOMY_MODE])
    detail = cast("AutonomyDetailLevel", context[CONTEXT_AUTONOMY_DETAIL])
    extra = provider.list_extra_directives(role=agent.role, department=agent.department)
    context["ask_policy"] = True
    context["ask_policy_section"] = build_ask_policy_section(
        provider.base_directive(autonomy=autonomy, detail=detail), extra
    )
    logger.debug(
        ASK_POLICY_PROMPT_INJECTED,
        agent_role=agent.role,
        agent_department=agent.department,
        autonomy_level=autonomy.value,
        detail_level=detail,
        extra_directive_count=len(extra),
    )


__all__ = [
    "CONTEXT_AUTONOMY_DETAIL",
    "CONTEXT_AUTONOMY_MODE",
    "inject_ask_policy_context",
    "should_inject_ask_policy",
]
