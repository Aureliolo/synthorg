# module-kind: declarative
"""What an engine is wired with, as one readable record.

The facts the engine's creation event logs, held as a value rather than
scattered across a log line: a harness that measures the engine has to be
able to ASK what it measured, because a log line is evidence only for whoever
captured it. The event and this record are one owner; the event is rendered
from it.

The tool surface is deliberately not here. It is final where the tool
invoker is built, once per run, and one engine serves many concurrent runs,
so a value held on the engine would name whichever run built its invoker
last; each run carries its own on its context (``AgentContext.tool_surface``).
The in-flight controls (compaction, stagnation, the approval gate) are
reported as wired only when they reach the loop that drives turns: an
injected loop was built elsewhere, so a control bound on the engine beside
it is held and never consulted.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineWiringSummary:
    """What one engine was constructed with.

    Attributes:
        loop_type: Which execution loop drives it.
        has_tool_registry: Whether a base tool registry was supplied.
        has_cost_tracker: Whether spend is recorded anywhere.
        has_budget_enforcer: Whether spend is bounded.
        has_coordinator: Whether multi-agent dispatch is wired.
        has_compaction_callback: Whether context is compacted at the fill
            threshold rather than truncated.
        has_stagnation_detector: Whether a looping session is watched.
        stagnation_strategy: What the detector is, or ``None`` when there is
            none, so "watched" and "watched by what" are one question.
        has_review_pipeline: Whether finished work is judged.
        has_memory_backend: Whether the agent recalls anything.
        has_sub_agent_runner: Whether delegation is wired.
        has_approval_gate: Whether a governed action can park for a human.
        has_policy_engine: Whether tool calls are evaluated against policy.
        cost_tracker: The tracker spend lands in, so a caller can check it
            IS the ledger it expects rather than a lookalike. ``None`` when
            nothing records spend.
    """

    loop_type: str
    has_tool_registry: bool
    has_cost_tracker: bool
    has_budget_enforcer: bool
    has_coordinator: bool
    has_compaction_callback: bool
    has_stagnation_detector: bool
    stagnation_strategy: str | None
    has_review_pipeline: bool
    has_memory_backend: bool
    has_sub_agent_runner: bool
    has_approval_gate: bool
    has_policy_engine: bool
    cost_tracker: object | None

    def log_fields(self) -> dict[str, str | bool | None]:
        """The summary as structured log fields, object references excluded.

        Returns:
            The fields the creation event carries.
        """
        return {
            "loop_type": self.loop_type,
            "has_tool_registry": self.has_tool_registry,
            "has_cost_tracker": self.has_cost_tracker,
            "has_budget_enforcer": self.has_budget_enforcer,
            "has_coordinator": self.has_coordinator,
            "has_compaction_callback": self.has_compaction_callback,
            "has_stagnation_detector": self.has_stagnation_detector,
            "stagnation_strategy": self.stagnation_strategy,
            "has_review_pipeline": self.has_review_pipeline,
            "has_memory_backend": self.has_memory_backend,
            "has_sub_agent_runner": self.has_sub_agent_runner,
            "has_approval_gate": self.has_approval_gate,
            "has_policy_engine": self.has_policy_engine,
        }


__all__ = ["EngineWiringSummary"]
