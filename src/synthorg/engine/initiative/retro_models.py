# module-kind: code
"""Domain models for the SHIP-time objective retrospective.

At objective completion the accountable lead distils a retrospective: a short
narrative plus two kinds of durable learning, one feeding organisational
memory (reusable procedures/conventions the whole company should carry
forward) and one feeding each contributing agent's own memory (what that
member should remember next time). These frozen models carry the distilled
result from the terminal ``submit_retrospective`` tool to the write side.
"""

import copy
from typing import Final, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import RetrospectiveParseError
from synthorg.memory.enums import OrgFactCategory
from synthorg.providers.models import ToolDefinition

#: Namespace for deriving the deterministic id a retrospective is tagged with,
#: so a duplicate capture of the same objective is recognised and skipped.
_RETRO_NAMESPACE = NAMESPACE_URL

#: Upper bound on each learning collection in one retrospective. Generous for a
#: genuine objective; a defence-in-depth cap so a runaway or adversarial tool
#: call cannot hand back an unbounded batch for the write loop to persist.
_MAX_LEARNINGS: Final[int] = 50

#: The kinds of org learning a retrospective may publish. Both are
#: agent-writable org-fact categories; core policy stays human-only, so a
#: retrospective can never write one.
OrgLearningKind = Literal["procedure", "convention"]

_KIND_TO_CATEGORY: dict[OrgLearningKind, OrgFactCategory] = {
    "procedure": OrgFactCategory.PROCEDURE,
    "convention": OrgFactCategory.CONVENTION,
}


def org_category_for(kind: OrgLearningKind) -> OrgFactCategory:
    """Map a learning kind onto its org-fact category.

    Returns:
        The :class:`OrgFactCategory` the learning is written under.
    """
    return _KIND_TO_CATEGORY[kind]


def initiative_contributor_ids(
    contributors: tuple[NotBlankStr, ...],
    lead_id: NotBlankStr,
) -> set[NotBlankStr]:
    """Return the agent ids a retrospective may write a personal learning for.

    A per-agent learning is only durable for someone who actually worked the
    objective, so a hallucinated or stale id in a submitted draft lands
    nowhere. ``contributors`` is derived from the tasks that ran on the
    initiative (``initiative_contributors``); the lead is unioned in because
    leading it is contributing to it even when no task carried their name.
    Colocated with the models it constrains even though the write side is
    where it is enforced.

    Returns:
        The set of contributor ids: everyone who worked it, plus the lead.
    """
    return set(contributors) | {lead_id}


def retro_object_tag(project_id: str) -> NotBlankStr:
    """Return the tag stamped on every fact from one objective's retrospective.

    The tag is how a re-run recognises an objective already has a
    retrospective and skips it, so it is deterministic in the project id.

    Returns:
        The ``objective:<uuid5>`` tag.
    """
    return NotBlankStr(f"objective:{uuid5(_RETRO_NAMESPACE, project_id)}")


class OrgLearning(BaseModel):
    """One reusable learning bound for organisational memory.

    Attributes:
        content: The learning, phrased as standing guidance the company
            should carry forward ("next time X, do Y").
        kind: Whether it is a procedure or a convention.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: NotBlankStr = Field(description="Reusable org-level learning")
    kind: OrgLearningKind = Field(description="Procedure or convention")


class AgentLearning(BaseModel):
    """One learning bound for a single contributing agent's own memory.

    Attributes:
        agent_id: The agent this learning belongs to.
        content: What that agent should remember from this objective.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Owning agent id")
    content: NotBlankStr = Field(description="Per-agent learning")


class RetrospectiveDraft(BaseModel):
    """The distilled retrospective a lead submits at objective completion.

    Attributes:
        summary: A short narrative of how the objective went.
        org_learnings: Reusable learnings bound for organisational memory.
        agent_learnings: Per-contributor learnings bound for agent memory.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    summary: NotBlankStr = Field(description="Short retrospective narrative")
    org_learnings: tuple[OrgLearning, ...] = Field(
        default=(),
        max_length=_MAX_LEARNINGS,
        description="Reusable learnings for org memory",
    )
    agent_learnings: tuple[AgentLearning, ...] = Field(
        default=(),
        max_length=_MAX_LEARNINGS,
        description="Per-agent learnings for agent memory",
    )


#: JSON schema for the ``submit_retrospective`` terminal tool. A module
#: constant (deep-copied per build) so the schema literal does not push
#: ``build_retrospective_tool`` past the function-length guideline.
_SUBMIT_RETRO_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A short, honest narrative of how the objective went.",
        },
        "org_learnings": {
            "type": "array",
            "description": (
                "Reusable lessons the whole organisation should carry "
                "forward. Phrase each as standing guidance, not a recount "
                "of this run. Omit anything an agent could rediscover by "
                "reading the codebase."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "kind": {"type": "string", "enum": ["procedure", "convention"]},
                },
                "required": ["content", "kind"],
            },
        },
        "agent_learnings": {
            "type": "array",
            "description": (
                "What each contributor should personally remember next "
                "time. Key by the agent's id; skip agents with nothing "
                "specific to learn."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["agent_id", "content"],
            },
        },
    },
    "required": ["summary"],
}


def build_retrospective_tool() -> ToolDefinition:
    """Build the terminal ``submit_retrospective`` tool definition.

    Returns:
        A ``ToolDefinition`` whose schema captures the summary plus the org
        and per-agent learnings.
    """
    return ToolDefinition(
        name="submit_retrospective",
        description=(
            "Submit the objective retrospective exactly once, last, after you "
            "have reviewed the finished work and recalled prior retros."
        ),
        parameters_schema=copy.deepcopy(_SUBMIT_RETRO_SCHEMA),
    )


def args_to_retrospective(args: dict[str, JsonValue]) -> RetrospectiveDraft:
    """Parse ``submit_retrospective`` arguments into a validated draft.

    Returns:
        The parsed :class:`RetrospectiveDraft`.

    Raises:
        RetrospectiveParseError: If the arguments are structurally invalid.
    """
    try:
        summary = _require_str(args, "summary")
        org = tuple(
            OrgLearning(
                content=NotBlankStr(_require_str(item, "content")),
                kind=cast("OrgLearningKind", _require_str(item, "kind")),
            )
            for item in _as_items(args.get("org_learnings"))
        )
        agents = tuple(
            AgentLearning(
                agent_id=NotBlankStr(_require_str(item, "agent_id")),
                content=NotBlankStr(_require_str(item, "content")),
            )
            for item in _as_items(args.get("agent_learnings"))
        )
        # Construct inside the try so the draft's own validation (the
        # per-collection max_length cap) surfaces as a retryable parse error the
        # lead can correct, not an uncaught ValidationError out of the session.
        return RetrospectiveDraft(
            summary=NotBlankStr(summary),
            org_learnings=org,
            agent_learnings=agents,
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = f"Invalid retrospective submission: {exc}"
        raise RetrospectiveParseError(msg) from exc


def _as_items(value: JsonValue | None) -> tuple[dict[str, JsonValue], ...]:
    """Coerce an optional JSON array of objects into a tuple of dicts.

    Returns:
        The array's object elements; empty when *value* is absent.

    Raises:
        TypeError: If *value* is present but not an array of objects.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "expected an array"
        raise TypeError(msg)
    items: list[dict[str, JsonValue]] = []
    for element in value:
        if not isinstance(element, dict):
            msg = "expected an array of objects"
            raise TypeError(msg)
        items.append(element)
    return tuple(items)


def _require_str(source: dict[str, JsonValue], key: str) -> str:
    """Return a non-blank string field from *source*.

    Returns:
        The stripped string value.

    Raises:
        KeyError: If the key is absent.
        ValueError: If the value is not a non-blank string.
    """
    value = source[key]
    if not isinstance(value, str) or not value.strip():
        msg = f"{key!r} must be a non-blank string"
        raise ValueError(msg)
    return value
