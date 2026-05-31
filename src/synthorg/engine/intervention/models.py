"""Typed models for mid-flight steering directives.

A steering directive is recorded as a project-brain ``PLAN_REVISION`` entry and
read by in-flight agents at safe boundaries. These frozen models are the typed
views and result envelopes the steering service, the inbox, and the front door
exchange; the durable record is the brain entry itself.

Tag convention on a steering brain entry:

* :data:`STEERING_TAG` (``"steering"``) is always present, so the inbox can
  project the active steering directives with a single indexed tag filter.
* :func:`steering_kind_tag` (e.g. ``"steering:redirect"``) carries the kind.
* :func:`task_narrow_tag` / :func:`agent_narrow_tag` carry optional narrowing;
  a directive with no narrowing tags applies to the whole project.
"""

from enum import StrEnum
from typing import Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.enums import InterventionKind
from synthorg.core.types import NotBlankStr

#: Umbrella tag every steering brain entry carries for the inbox projection.
STEERING_TAG: Final[NotBlankStr] = NotBlankStr("steering")

#: Prefix for the per-kind discriminator tag (e.g. ``steering:redirect``).
_KIND_TAG_PREFIX: Final[str] = "steering:"

#: Prefix for an optional task-narrowing tag.
_TASK_NARROW_PREFIX: Final[str] = "steer-task:"

#: Prefix for an optional agent-narrowing tag.
_AGENT_NARROW_PREFIX: Final[str] = "steer-agent:"

#: Intervention kinds the steering subsystem propagates into running agents.
STEERABLE_KINDS: Final[frozenset[InterventionKind]] = frozenset(
    {InterventionKind.HINT, InterventionKind.REDIRECT},
)

#: Pre-computed value set for steering-kind tag parsing (avoids rebuilding the
#: comprehension on every ``parse_steering_tags`` call).
_STEERABLE_VALUES: Final[frozenset[str]] = frozenset(k.value for k in STEERABLE_KINDS)

#: Metadata keys a ``CONVERSATIONAL_INTAKE`` approval carries when the Chief of
#: Staff parks a steering directive. The approval-gate Flow 0 reads them to
#: route an approved directive to ``SteeringService.issue``; the presence of
#: :data:`STEERING_INTAKE_KIND_KEY` marks an approval as a steering directive.
STEERING_INTAKE_KIND_KEY: Final[str] = "steering_kind"
STEERING_INTAKE_PROJECT_KEY: Final[str] = "steering_project"
STEERING_INTAKE_TEXT_KEY: Final[str] = "steering_text"


def steering_kind_tag(kind: InterventionKind) -> NotBlankStr:
    """Return the per-kind steering tag, e.g. ``steering:redirect``.

    Returns:
        The discriminator tag for ``kind``.
    """
    return NotBlankStr(f"{_KIND_TAG_PREFIX}{kind.value}")


def task_narrow_tag(task_id: NotBlankStr) -> NotBlankStr:
    """Return the task-narrowing tag for ``task_id``.

    Returns:
        The ``steer-task:<id>`` tag.
    """
    return NotBlankStr(f"{_TASK_NARROW_PREFIX}{task_id}")


def agent_narrow_tag(agent_id: NotBlankStr) -> NotBlankStr:
    """Return the agent-narrowing tag for ``agent_id``.

    Returns:
        The ``steer-agent:<id>`` tag.
    """
    return NotBlankStr(f"{_AGENT_NARROW_PREFIX}{agent_id}")


def parse_steering_tags(
    tags: tuple[NotBlankStr, ...],
) -> tuple[InterventionKind | None, tuple[NotBlankStr, ...], tuple[NotBlankStr, ...]]:
    """Extract ``(kind, narrow_task_ids, narrow_agent_ids)`` from entry tags.

    Args:
        tags: The full tag tuple of a steering brain entry.

    Returns:
        The parsed kind (``None`` when no recognised kind tag is present),
        the narrowed task ids, and the narrowed agent ids.
    """
    kind: InterventionKind | None = None
    task_ids: list[NotBlankStr] = []
    agent_ids: list[NotBlankStr] = []
    for tag in tags:
        if tag == STEERING_TAG:
            continue
        if tag.startswith(_TASK_NARROW_PREFIX):
            task_ids.append(NotBlankStr(tag[len(_TASK_NARROW_PREFIX) :]))
        elif tag.startswith(_AGENT_NARROW_PREFIX):
            agent_ids.append(NotBlankStr(tag[len(_AGENT_NARROW_PREFIX) :]))
        elif tag.startswith(_KIND_TAG_PREFIX):
            value = tag[len(_KIND_TAG_PREFIX) :]
            if value in _STEERABLE_VALUES:
                kind = InterventionKind(value)
    return kind, tuple(task_ids), tuple(agent_ids)


class SupersedeMode(StrEnum):
    """How a steering directive treats now-obsolete sibling tasks.

    ``NONE`` cancels nothing. ``EXPLICIT`` cancels the supplied task ids
    immediately at issue time. ``PROPOSE`` runs the pluggable proposer to
    refine the obsolete set and returns it for the operator to confirm or edit
    before any cancellation; the proposer never cancels.
    """

    NONE = "none"
    EXPLICIT = "explicit"
    PROPOSE = "propose"


class ActiveSteeringDirective(BaseModel):
    """A live steering directive an in-flight agent can adopt.

    Built by the inbox from a project-brain entry tagged :data:`STEERING_TAG`.
    The ``text`` is the raw operator directive as stored in the brain; the
    injection sites wrap it via ``wrap_untrusted(TAG_BRAIN_STATE, ...)`` when it
    re-enters agent context.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_id: NotBlankStr = Field(
        description="Brain entry id (stable across revisions)",
    )
    kind: InterventionKind = Field(description="HINT or REDIRECT")
    text: NotBlankStr = Field(description="The operator directive text (raw)")
    author: NotBlankStr = Field(description="Operator or agent id that issued it")
    recorded_at: AwareDatetime = Field(description="When the directive was recorded")
    narrow_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional task-id narrowing; empty means project-wide",
    )
    narrow_agent_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Optional agent-id narrowing; empty means every agent",
    )

    @model_validator(mode="after")
    def _validate_steerable_kind(self) -> Self:
        """Reject PAUSE/KILL: only HINT and REDIRECT propagate into agents.

        Returns:
            ``self`` when the kind is steerable.

        Raises:
            ValueError: When ``kind`` is not a steerable intervention.
        """
        if self.kind not in STEERABLE_KINDS:
            msg = f"{self.kind.value!r} is not a steerable directive kind"
            raise ValueError(msg)
        return self

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether adopting this directive forces a replan",
    )
    @property
    def requires_replan(self) -> bool:
        """A REDIRECT forces a replan; a HINT is advisory only."""
        return self.kind is InterventionKind.REDIRECT

    def applies_to(
        self,
        *,
        task_id: str | None,
        agent_id: str | None,
    ) -> bool:
        """Whether this directive applies to the given task/agent.

        A directive with no narrowing applies to every task and agent on the
        project. Narrowing is a positive filter: when ``narrow_task_ids`` is
        non-empty the task must be listed, and likewise for agents.

        Returns:
            ``True`` when the directive applies to the running agent/task.
        """
        task_excluded = self.narrow_task_ids and (
            task_id is None or task_id not in self.narrow_task_ids
        )
        agent_excluded = self.narrow_agent_ids and (
            agent_id is None or agent_id not in self.narrow_agent_ids
        )
        return not (task_excluded or agent_excluded)


class SteeringSupersessionProposal(BaseModel):
    """A proposed set of obsolete tasks for operator confirmation.

    Returned by ``SteeringService.issue`` in ``PROPOSE`` mode. Nothing is
    cancelled until the operator confirms (and may edit) the set via
    ``confirm_supersession``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    directive_id: NotBlankStr = Field(description="The directive this refines")
    proposed_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tasks the proposer judged obsolete",
    )
    rationale: str = Field(
        default="",
        description="Why these tasks are obsolete (operator-facing)",
    )


class SteeringIssueResult(BaseModel):
    """Outcome of issuing a steering directive."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    directive_id: NotBlankStr = Field(
        description="Brain entry id of the recorded directive",
    )
    kind: InterventionKind = Field(description="The intervention kind issued")
    superseded_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tasks cancelled immediately (EXPLICIT mode)",
    )
    proposal: SteeringSupersessionProposal | None = Field(
        default=None,
        description="Proposed obsolete set awaiting confirmation (PROPOSE mode)",
    )
