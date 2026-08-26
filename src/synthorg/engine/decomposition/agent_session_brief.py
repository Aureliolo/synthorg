# module-kind: code
"""The words a planning session is given.

Kept beside the session rather than inside it so the prompt text reads as
one piece: what the planner may call, which roles it may assign, and what
a good plan looks like are three halves of the same instruction, and a
change to one usually wants a look at the others.
"""

from typing import Final

from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext, width_budget
from synthorg.engine.decomposition.llm_prompt import (
    DECOMPOSITION_FENCES,
    OBJECTIVE_CRITERIA_LABEL,
    coverage_guidance,
    foundation_lines,
    safe_roles,
)
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted


def toolkit_lines(granted_tools: tuple[str, ...]) -> tuple[str, ...]:
    """Render the brief's account of what this session can actually call.

    Derived from the built registry rather than written out, so the brief
    cannot advertise a toolkit the session does not hold. Told to guess, the
    planner reached for a progressive-disclosure trio (``list_tools``,
    ``load_tool``, ``load_tool_resource``) it was never granted and burned two
    rounds on tool-not-found before producing nothing.

    Args:
        granted_tools: Names of every tool in the session's registry.

    Returns:
        The brief lines naming the toolkit.
    """
    if not granted_tools:
        return ("You have no tools: plan from the objective alone.",)
    return (
        "You can call exactly these tools, directly, with no discovery step:",
        f"  {', '.join(granted_tools)}.",
        "There is no tool catalogue to list or load from; anything not named",
        "above does not exist in this session. Research with what you have,",
        "and where the plan turns on an external fact you cannot check, record",
        "it as an assumption rather than guessing silently.",
    )


def roster_lines(available_roles: tuple[NotBlankStr, ...]) -> tuple[str, ...]:
    """Render the roster constraint for the planning brief.

    Stated in the brief as well as in the submit tool's schema, because the
    schema ``enum`` only reaches a provider that enforces schemas, and left to
    guess the planner produces plausible near-misses (an "Engineer" title for
    an org staffing a "Developer" one) that nothing can be dispatched to.

    Args:
        available_roles: The roles the org staffs.

    Returns:
        The brief lines, or empty when no roster is known.
    """
    if not available_roles:
        return ()
    return (
        "  This organisation staffs exactly these roles:",
        f"  {', '.join(safe_roles(available_roles))}.",
        "  Every owner must be one of them, spelled the same way. Do not",
        "  invent a role or substitute a similar-sounding title; an owner",
        "  outside this list is rejected.",
    )


#: Every fence tag :func:`planning_brief` can emit, which is the same set the
#: single-shot decomposer emits because both share :func:`foundation_lines`.
PLANNING_SESSION_FENCES: Final[tuple[str, ...]] = DECOMPOSITION_FENCES


def planning_brief(
    task: Task,
    context: DecompositionContext,
    granted_tools: tuple[str, ...],
) -> str:
    """Compose the planning instruction with the fenced objective.

    The objective text originates from operator/charter input and is
    attacker-controllable, so it is fenced via ``wrap_untrusted``; the
    instructions and numeric constraints sit outside the fence.

    Args:
        task: The objective being planned.
        context: Roster and size constraints for this decomposition.
        granted_tools: Names of every tool in the session's registry.

    Returns:
        The user-message brief driving the planning session.
    """
    inner = [f"Title: {task.title}", f"Description: {task.description}"]
    own = tuple(c.description for c in task.acceptance_criteria)
    # The submit tool validates ``satisfies`` against the level's inherited
    # vocabulary, so the brief has to render it or the planner is judged on a
    # list it was never shown. At the root the two coincide and it is rendered
    # once, under the heading the tool schema names.
    if own and own != tuple(context.objective_criteria):
        inner.append("Acceptance criteria:")
        inner.extend(f"  - {description}" for description in own)
    if context.objective_criteria:
        inner.append(OBJECTIVE_CRITERIA_LABEL)
        inner.extend(f"  - {criterion}" for criterion in context.objective_criteria)
    return "\n".join(
        [
            "You are the accountable owner planning this objective. Produce",
            "a plan a team would execute, not a flat checklist.",
            *toolkit_lines(granted_tools),
            "Then build the plan:",
            "- Model real structure: add a dependency ONLY when one item",
            "  genuinely cannot start until another finishes; independent",
            "  workstreams must run in parallel (task_structure mixed or",
            "  parallel, not a single sequential chain).",
            "- Split by DELIVERABLE, never by phase or role. One item is one",
            "  agent's whole job, its tests included: 'write the parser' and",
            "  'test the parser' are one item, not two. Splitting by type of",
            "  work hands one piece of context to several agents and makes",
            "  them coordinate to get it back.",
            "- Assign an accountable owning role to every item; leave none",
            "  unowned.",
            *roster_lines(context.available_roles),
            "- Calibrate: most items are normal stakes; reserve high or",
            "  critical for irreversible or high-blast-radius work.",
            "- Give every item concrete expected_artifacts and verifiable",
            "  acceptance_criteria (never empty).",
            # Stated here as well as in the tool schema, for the reason the
            # roster is: the schema's description reaches only a provider that
            # enforces schemas.
            coverage_guidance(covers_objective=bool(context.objective_criteria)).rstrip(
                "\n"
            ),
            *foundation_lines(context.workspace_summary),
            "- Where the plan hinges on a real choice (stack, architecture),",
            "  surface a decision item (kind 'decision') with 2-4 options and",
            "  one recommended, rather than silently deciding.",
            "Then critically self-review: is it genuinely parallel where it",
            "can be, is every item owned, are stakes calibrated (not all",
            "high), does every item define done? Finally, call",
            "submit_decomposition_plan exactly once with the complete plan.",
            "",
            wrap_untrusted(TAG_TASK_DATA, "\n".join(inner)),
            "",
            "Constraints:",
            f"  max_subtasks: {width_budget(context)}",
        ]
    )
