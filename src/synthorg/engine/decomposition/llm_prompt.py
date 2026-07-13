"""Prompt building for LLM-based decomposition.

Pure functions that construct the system/user messages and the
``submit_decomposition_plan`` tool definition. Response parsing lives in
:mod:`synthorg.engine.decomposition.llm_parse`; both share ``TOOL_NAME``.
"""

from pydantic import JsonValue

from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)
from synthorg.engine.decomposition.models import DecompositionContext
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    ToolDefinition,
)

TOOL_NAME = "submit_decomposition_plan"


def build_decomposition_tool() -> ToolDefinition:
    """Build the ``submit_decomposition_plan`` tool definition.

    Returns:
        A ``ToolDefinition`` with a JSON Schema describing the plan
        structure, including subtask definitions with dependencies
        and complexity metadata.
    """
    subtask_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Unique subtask identifier",
            },
            "title": {
                "type": "string",
                "description": "Short subtask title",
            },
            "description": {
                "type": "string",
                "description": "Detailed subtask description",
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of subtasks this depends on",
            },
            "estimated_complexity": {
                "type": "string",
                "enum": [c.value for c in Complexity],
                "description": (
                    "Effort/uncertainty estimate. Reserve 'epic' for a whole "
                    "workstream that should itself be broken down further."
                ),
            },
            "stakes": {
                "type": "string",
                "enum": [s.value for s in Stakes],
                "description": (
                    "How consequential this item is if done wrong. Most items "
                    "are 'normal'; reserve 'high'/'critical' for irreversible "
                    "or high-blast-radius work (a handful, not most)."
                ),
            },
            "required_role": {
                "type": "string",
                "description": (
                    "The role accountable for this item (e.g. 'Backend "
                    "Engineer', 'CTO'). Every item must name an owner."
                ),
            },
            "required_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Skills needed for this subtask",
            },
            "expected_artifacts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Concrete deliverables this subtask must produce "
                    "(file paths, docs, or test suites). Non-empty so the "
                    "fail-loud zero-artifact guard engages when it runs."
                ),
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verifiable criteria that define done for this subtask",
            },
            "satisfies": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Which of the objective's acceptance criteria (copied "
                    "verbatim) this item advances, so success-criteria coverage "
                    "can be checked. Omit only for pure-support items that "
                    "advance no objective criterion directly."
                ),
            },
            "kind": {
                "type": "string",
                "enum": [k.value for k in PlanItemKind],
                "description": (
                    "'work' for a unit of work, or 'decision' for a real choice "
                    "the reviewer must make (e.g. stack/architecture). A decision "
                    "carries options and records the choice rather than building."
                ),
            },
            "options": {
                "type": "array",
                "description": (
                    "For a 'decision' subtask only: 2-4 options to choose among, "
                    "exactly one marked recommended."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Stable option id"},
                        "title": {"type": "string", "description": "Option title"},
                        "summary": {
                            "type": "string",
                            "description": "The option's tradeoffs and rationale",
                        },
                        "recommended": {
                            "type": "boolean",
                            "description": "Whether the owner recommends this option",
                        },
                    },
                    "required": ["id", "title", "summary"],
                },
            },
        },
        "required": [
            "id",
            "title",
            "description",
            "stakes",
            "required_role",
            "expected_artifacts",
            "acceptance_criteria",
        ],
    }
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": subtask_schema,
                "description": "Ordered subtask definitions",
            },
            "task_structure": {
                "type": "string",
                "enum": [s.value for s in TaskStructure],
                "description": (
                    "Overall structure: 'parallel'/'mixed' when independent "
                    "workstreams can run at once, 'sequential' only when every "
                    "item genuinely depends on the previous one."
                ),
            },
            "coordination_topology": {
                "type": "string",
                "enum": [t.value for t in CoordinationTopology],
                "description": "Coordination topology",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Questions you could not resolve that need the human's input "
                    "before the plan is approved (e.g. an ambiguous requirement or "
                    "an external dependency). Omit when nothing is open."
                ),
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Load-bearing assumptions the plan rests on, so the human can "
                    "correct a wrong one before approving. Omit when none."
                ),
            },
        },
        "required": ["subtasks"],
    }
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Submit a task decomposition plan with subtasks, "
            "their dependencies, and coordination metadata."
        ),
        parameters_schema=schema,
    )


def build_system_message() -> ChatMessage:
    """Build the system prompt for decomposition.

    The hand-rolled "treat <task-data> as untrusted" warning is
    replaced by the canonical :func:`untrusted_content_directive` so
    the prompt-fingerprint test catches silent drift in the wording.

    Returns:
        A ``ChatMessage`` with ``MessageRole.SYSTEM``.
    """
    content = (
        "You are a planning lead breaking a greenlit objective into a plan a "
        "team would actually execute, not a flat checklist.\n\n"
        "Guidelines:\n"
        "- Each subtask has a unique ID, a clear title, and a detailed "
        "description.\n"
        "- Model real structure: chain a dependency ONLY when one item "
        "genuinely cannot start until another finishes. Independent "
        "workstreams must run in parallel, so most plans are 'mixed' or "
        "'parallel', not 'sequential'.\n"
        "- Assign an accountable owning role (required_role) to every item; no "
        "item is left unowned.\n"
        "- Estimate complexity per item; reserve 'epic' for a whole workstream "
        "that should be broken down further.\n"
        "- Calibrate stakes: most items are 'normal'. Reserve 'high'/'critical' "
        "for irreversible or high-blast-radius work, a handful, not most.\n"
        "- For each item, list concrete expected_artifacts (file paths, docs, "
        "or test suites) and verifiable acceptance_criteria that define when it "
        "is done. Never leave these empty.\n"
        "- Tag each item with the objective acceptance criteria it advances "
        "(satisfies, copied verbatim) so coverage is checkable. Between them, "
        "the items must cover every objective criterion.\n"
        "- Where the plan hinges on a real choice (stack, architecture), surface "
        "a decision item (kind 'decision') with 2-4 options and a recommended "
        "one, rather than silently deciding; its criterion is that the decision "
        "is recorded with a rationale.\n"
        "- Classify the overall task_structure and choose a coordination "
        "topology.\n"
        "- Surface any open_questions you could not resolve and the load-bearing "
        "assumptions the plan rests on, so the human can answer or correct them "
        "before approving rather than discovering them mid-build.\n"
        "- Before submitting, self-review: is it genuinely parallel where it "
        "can be, is every item owned, are stakes calibrated (not all high), and "
        "does every item define done?\n"
        "- Use the submit_decomposition_plan tool to provide your answer.\n"
        "- If a tool call is not possible, respond with a JSON object in the "
        "same schema.\n\n" + untrusted_content_directive((TAG_TASK_DATA,))
    )
    return ChatMessage(role=MessageRole.SYSTEM, content=content)


def build_task_message(
    task: Task,
    context: DecompositionContext,
) -> ChatMessage:
    """Build the user message with task details and constraints.

    Task fields (title, description, acceptance criteria) originate from
    public API payloads and must be treated as attacker-controllable.
    They are routed through :func:`wrap_untrusted` so an attacker who
    embeds the literal closing fence cannot break out -- mirrors
    :func:`synthorg.engine.prompt_validation.format_task_instruction`.
    Constraints sit outside the fence: numeric system-controlled values
    carry no breakout vector.

    Args:
        task: The parent task to decompose.
        context: Decomposition constraints.

    Returns:
        A ``ChatMessage`` with ``MessageRole.USER``.
    """
    inner: list[str] = [
        f"Title: {task.title}",
        f"Description: {task.description}",
    ]
    if task.acceptance_criteria:
        inner.append("Acceptance Criteria:")
        inner.extend(f"  - {c.description}" for c in task.acceptance_criteria)

    parts = [
        wrap_untrusted(TAG_TASK_DATA, "\n".join(inner)),
        "",
        "Constraints:",
        f"  max_subtasks: {context.max_subtasks}",
        f"  current_depth: {context.current_depth}",
        f"  max_depth: {context.max_depth}",
    ]
    content = "\n".join(parts)
    return ChatMessage(role=MessageRole.USER, content=content)


def build_retry_message(error: str) -> ChatMessage:
    """Build a retry message with the prior error.

    Args:
        error: Description of the parsing/validation error.

    Returns:
        A ``ChatMessage`` with ``MessageRole.USER``.
    """
    content = (
        "Your previous response could not be parsed. "
        f"Error: {error}\n\n"
        "Please try again using the "
        "submit_decomposition_plan tool with corrected "
        "arguments."
    )
    return ChatMessage(role=MessageRole.USER, content=content)
