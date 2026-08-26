# module-kind: declarative
"""The JSON Schema a decomposition plan is submitted against.

Data, not logic: every entry is a literal describing one field of the plan the
planner returns. Kept beside :mod:`synthorg.engine.decomposition.llm_prompt`
rather than inside it because the two are read for different reasons, and a
180-line literal in the middle of the prompt text buries both.

Two fields are NOT here, because they are the two the level changes rather than
constants: ``required_role`` turns on the roster, and ``satisfies`` on whether
this level is answerable for any objective criterion at all. Both are composed
in by :func:`~synthorg.engine.decomposition.llm_prompt.build_decomposition_tool`.
"""

from typing import Final

from pydantic import JsonValue

from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)

#: Every subtask property except ``required_role`` and ``satisfies``. Treated
#: as read-only; the builder composes a fresh outer mapping around it and never
#: mutates it.
SUBTASK_PROPERTIES: Final[dict[str, JsonValue]] = {
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
    "required_skills": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Skills needed for this subtask",
    },
    # No minItems: a 'decision' item builds nothing and MUST declare an empty
    # list, so a schema-level floor of one would make a decision item
    # unsatisfiable and push a schema-enforcing provider into emitting
    # artifacts the parser then rejects. The kind-dependent invariant is
    # stated here and enforced by ``validate_expected_artifacts`` at parse
    # time, where it can see the kind.
    "expected_artifacts": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Concrete deliverables this subtask must produce "
            "(file paths, docs, or test suites). A 'work' item must "
            "list at least one and the plan is rejected without it, "
            "because the fail-loud zero-artifact guard engages off "
            "this list when the item runs. A 'decision' item builds "
            "nothing and must leave this empty."
        ),
    },
    "acceptance_criteria": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Verifiable criteria that define done for this subtask, each "
            "decidable from this item's own expected_artifacts plus those "
            "of the items it depends on. Naming a file a later item "
            "produces makes the criterion unjudgeable and the plan is "
            "rejected at parse time."
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
}

#: The subtask fields a plan is rejected without.
SUBTASK_REQUIRED: Final[list[JsonValue]] = [
    "id",
    "title",
    "description",
    "stakes",
    "required_role",
    "expected_artifacts",
    "acceptance_criteria",
]

#: Every plan-level property except ``subtasks``, which is the one that
#: embeds the roster-dependent subtask schema.
PLAN_PROPERTIES: Final[dict[str, JsonValue]] = {
    "task_structure": {
        "type": "string",
        # AUTO is the absence of a declaration, so offering it as a choice
        # would invite the planner to punt on a field it is better placed to
        # answer than the keyword classifier that otherwise fills the gap.
        # Omitting the field says the same thing without dressing it as an
        # answer.
        "enum": [s.value for s in TaskStructure if s is not TaskStructure.AUTO],
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
}

__all__ = [
    "PLAN_PROPERTIES",
    "SUBTASK_PROPERTIES",
    "SUBTASK_REQUIRED",
]
