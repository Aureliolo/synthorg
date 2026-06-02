# module-kind: declarative
"""Domain models for the run-narrative engine.

Three layers flow through the narrator:

* :class:`RunNarrativeInputs` is the raw material the reader gathers from
  the flight recorder, the project brain, and the task record.
* :class:`ReducedRun` is the deterministic rollup the reducer produces:
  the facts, already shaped into the blocks the assembler will emit.
* :class:`NarrativeProse` is the connective prose the synthesiser asks
  the LLM for; it carries no facts, only narration.
"""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.project_brain.models import BrainEntry, BrainSummary

# ── Bounded text aliases ─────────────────────────────────────────────

NarrativeProseText = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
"""Bounded non-empty prose paragraph."""

ShortLabel = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded non-empty label (decision title, metric name, source label)."""

MetricValue = Annotated[str, StringConstraints(min_length=1, max_length=128)]
"""Bounded non-empty metric value rendered as a string."""

LinkRef = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded non-empty link target (relative anchor or absolute URL)."""


# ── Reader output ────────────────────────────────────────────────────


class AgentTurnTally(BaseModel):
    """Per-agent rollup of frame activity (reader-side, pre-bounding)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Contributing agent")
    turn_count: int = Field(ge=1, description="Turns this agent produced")
    cost: float = Field(ge=0.0, description="Summed cost of this agent's turns")
    tools: tuple[str, ...] = Field(
        default=(),
        description="Distinct tool names this agent invoked",
    )


class RunNarrativeInputs(BaseModel):
    """Raw material gathered for one run, before any reduction.

    ``decisions`` carry full :class:`BrainEntry` envelopes (rationale,
    payload, citations are all needed downstream); ``open_items`` carry
    lightweight :class:`BrainSummary` projections (only title / kind /
    status are rendered).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    task_id: NotBlankStr = Field(description="Brief / root task id")
    execution_id: NotBlankStr = Field(description="Resolved execution id")
    brief_title: NotBlankStr = Field(description="Human-readable brief title")
    final_status: TaskStatus = Field(description="Terminal task status")
    total_cost: float = Field(ge=0.0, description="Summed run cost")
    total_turns: int = Field(ge=0, description="Highest turn index recorded")
    frame_count: int = Field(ge=0, description="Frames pulled for the run")
    decisions: tuple[BrainEntry, ...] = Field(
        default=(),
        description="Full decision entries recorded against the run",
    )
    open_items: tuple[BrainSummary, ...] = Field(
        default=(),
        description="Still-live questions, blockers, risks, dependencies",
    )
    agent_turns: tuple[AgentTurnTally, ...] = Field(
        default=(),
        description="Per-agent turn / tool / cost tallies from the frames",
    )


# ── Reducer output ───────────────────────────────────────────────────


class SourceRef(BaseModel):
    """One provenance pointer rendered in the Sources section.

    ``url`` is a relative anchor for internal references (task, brain
    entry, doc, knowledge source) or an absolute URL for external links;
    the assembler renders it as a :class:`LinkBlock`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    label: ShortLabel = Field(description="Human-readable source label")
    url: LinkRef = Field(description="Relative anchor or absolute URL")
    kind: NotBlankStr = Field(description="Provenance kind (task, entry, ...)")


class ReducedDecision(BaseModel):
    """A decision shaped for verbatim rendering, with its sources."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: ShortLabel = Field(description="Decision title")
    outcome: NarrativeProseText = Field(description="The option chosen")
    rationale: NarrativeProseText = Field(description="Why this decision")
    alternatives: tuple[ShortLabel, ...] = Field(
        default=(),
        description="Options considered and not chosen",
    )
    sources: tuple[SourceRef, ...] = Field(
        default=(),
        description="Provenance backing this decision",
    )


class AgentContribution(BaseModel):
    """A bounded per-agent contribution line for the roster."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Contributing agent")
    turn_count: int = Field(ge=1, description="Turns produced")
    cost: float = Field(ge=0.0, description="Summed cost")
    tools: tuple[str, ...] = Field(default=(), description="Tools invoked")


class OpenItem(BaseModel):
    """A still-live question / blocker / risk / dependency line."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: NotBlankStr = Field(description="Item kind (open_question, risk, ...)")
    title: ShortLabel = Field(description="Item title")
    status: NotBlankStr = Field(description="Lifecycle status")


class RunMetric(BaseModel):
    """A single run metric rendered as a metric block."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: ShortLabel = Field(description="Metric label")
    value: MetricValue = Field(description="Metric value as a string")
    unit: NotBlankStr | None = Field(default=None, description="Optional unit")


class ReducedRun(BaseModel):
    """The deterministic, fact-only rollup the assembler renders.

    Every field here is sourced verbatim from the brain or the flight
    recorder. The LLM never touches this model; it only contributes the
    :class:`NarrativeProse` woven around it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    task_id: NotBlankStr = Field(description="Brief / root task id")
    execution_id: NotBlankStr = Field(description="Execution id")
    brief_title: NotBlankStr = Field(description="Brief title")
    final_status: TaskStatus = Field(description="Terminal task status")
    metrics: tuple[RunMetric, ...] = Field(
        default=(),
        description="Run metrics (turns, cost, agents, status)",
    )
    decisions: tuple[ReducedDecision, ...] = Field(
        default=(),
        description="Decisions taken, newest-first, bounded",
    )
    contributions: tuple[AgentContribution, ...] = Field(
        default=(),
        description="Who did what, by contribution volume, bounded",
    )
    outcomes: tuple[ShortLabel, ...] = Field(
        default=(),
        description="Outcome bullet lines",
    )
    open_items: tuple[OpenItem, ...] = Field(
        default=(),
        description="Standing items still live at run end",
    )
    sources: tuple[SourceRef, ...] = Field(
        default=(),
        description="Consolidated provenance for the Sources section",
    )


# ── Synthesiser output ───────────────────────────────────────────────


class NarrativeProse(BaseModel):
    """Connective prose from the LLM. Carries narration, never facts.

    Each field is an optional paragraph woven beneath the matching
    section heading; ``summary`` always renders (a deterministic fallback
    fills it when the provider call fails).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    summary: NarrativeProseText = Field(description="Executive summary paragraph")
    decisions: NarrativeProseText | None = Field(
        default=None,
        description="Narration introducing the decisions section",
    )
    contributions: NarrativeProseText | None = Field(
        default=None,
        description="Narration introducing the contributions section",
    )
    outcomes: NarrativeProseText | None = Field(
        default=None,
        description="Narration introducing the outcomes section",
    )
