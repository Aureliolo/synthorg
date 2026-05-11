"""Execution loop auto-selection based on task complexity and budget state.

Provides ``AutoLoopConfig`` and ``AutoLoopRule`` Pydantic models for
configuring selection rules, a pure ``select_loop_type`` function that
maps task complexity and optional budget utilization to a loop type
string, and a ``build_execution_loop`` factory that instantiates the
concrete loop.

The default rules follow the design spec (section 6.5):
simple -> ReAct, medium -> Plan-and-Execute, complex/epic -> Hybrid.
When budget utilization is at or above ``budget_tight_threshold``,
hybrid selections are downgraded to plan_execute.  An optional
``hybrid_fallback`` can redirect hybrid to another loop type.
"""

from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.enums import Complexity
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.react_loop import ReactLoop
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_BUDGET_DOWNGRADE,
    EXECUTION_LOOP_HYBRID_FALLBACK,
    EXECUTION_LOOP_NO_RULE_MATCH,
    EXECUTION_LOOP_UNKNOWN_TYPE,
)

if TYPE_CHECKING:
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.checkpoint.callback import CheckpointCallback
    from synthorg.engine.compaction import CompactionCallback
    from synthorg.engine.hybrid_models import HybridLoopConfig
    from synthorg.engine.loop_protocol import ExecutionLoop
    from synthorg.engine.plan_models import PlanExecuteConfig
    from synthorg.engine.stagnation import StagnationDetector

logger = get_logger(__name__)

_KNOWN_LOOP_TYPES: frozenset[str] = frozenset({"react", "plan_execute", "hybrid"})
"""Loop type identifiers recognized by the auto-selection system."""

_BUILDABLE_LOOP_TYPES: frozenset[str] = frozenset(
    {"react", "plan_execute", "hybrid"},
)
"""Loop types that ``build_execution_loop`` can instantiate."""


class AutoLoopRule(BaseModel):
    """Maps a task complexity level to an execution loop type.

    Attributes:
        complexity: The task complexity this rule matches.
        loop_type: One of the known loop types (``"react"``,
            ``"plan_execute"``, ``"hybrid"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    complexity: Complexity = Field(description="Task complexity level")
    loop_type: NotBlankStr = Field(description="Loop type identifier")

    @field_validator("loop_type")
    @classmethod
    def _validate_known_loop_type(cls, v: str) -> str:
        """Reject loop types not in the known set."""
        if v not in _KNOWN_LOOP_TYPES:
            msg = f"Unknown loop_type {v!r}; allowed: {sorted(_KNOWN_LOOP_TYPES)}"
            raise ValueError(msg)
        return v


DEFAULT_AUTO_LOOP_RULES: tuple[AutoLoopRule, ...] = (
    AutoLoopRule(complexity=Complexity.SIMPLE, loop_type="react"),
    AutoLoopRule(complexity=Complexity.MEDIUM, loop_type="plan_execute"),
    AutoLoopRule(complexity=Complexity.COMPLEX, loop_type="hybrid"),
    AutoLoopRule(complexity=Complexity.EPIC, loop_type="hybrid"),
)

# Import-time completeness guard (follows _SENIORITY_ORDER pattern in
# core/enums.py): ensures every Complexity member has a default rule.
_covered = {r.complexity for r in DEFAULT_AUTO_LOOP_RULES}
_all_complexities = set(Complexity)
if _covered != _all_complexities:
    _missing = _all_complexities - _covered
    msg = f"DEFAULT_AUTO_LOOP_RULES missing complexities: {_missing}"
    raise RuntimeError(msg)


class AutoLoopConfig(BaseModel):
    """Configuration for automatic execution loop selection.

    Attributes:
        rules: Ordered rules mapping complexity to loop type.
            Each complexity must appear at most once.  All
            ``loop_type`` values must be in ``_KNOWN_LOOP_TYPES``.
        budget_tight_threshold: Monthly budget utilization percentage
            at or above which the budget is considered tight.  When
            tight, hybrid selections are downgraded to plan_execute.
        hybrid_fallback: Optional override loop type when hybrid is
            selected.  ``None`` keeps the hybrid selection (default).
            Must be a known loop type when not ``None``.
        default_loop_type: Fallback loop type when no rule matches a
            task's complexity.  Must be a known loop type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rules: tuple[AutoLoopRule, ...] = Field(
        default=DEFAULT_AUTO_LOOP_RULES,
        description="Complexity-to-loop mapping rules",
    )
    budget_tight_threshold: int = Field(
        default=80,
        ge=0,
        le=100,
        description="Budget utilization % that triggers tight-budget mode",
    )
    hybrid_fallback: NotBlankStr | None = Field(
        default=None,
        description=(
            "Optional fallback loop when hybrid is selected. "
            "``None`` keeps the hybrid selection (default)."
        ),
    )
    default_loop_type: NotBlankStr = Field(
        default="react",
        description="Fallback loop when no rule matches a task complexity",
    )

    @model_validator(mode="after")
    def _validate_rules_and_fallbacks(self) -> Self:
        """Validate unique complexities, known types, and buildability."""
        seen: set[Complexity] = set()
        for rule in self.rules:
            if rule.complexity in seen:
                msg = f"Duplicate complexity in rules: {rule.complexity.value!r}"
                raise ValueError(msg)
            if rule.loop_type not in _KNOWN_LOOP_TYPES:
                msg = f"Unknown loop type in rules: {rule.loop_type!r}"
                raise ValueError(msg)
            seen.add(rule.complexity)
        if (
            self.hybrid_fallback is not None
            and self.hybrid_fallback not in _KNOWN_LOOP_TYPES
        ):
            msg = f"Unknown hybrid_fallback: {self.hybrid_fallback!r}"
            raise ValueError(msg)
        if self.default_loop_type not in _KNOWN_LOOP_TYPES:
            msg = f"Unknown default_loop_type: {self.default_loop_type!r}"
            raise ValueError(msg)
        # hybrid_fallback itself must be buildable (it is the redirect
        # target -- an unbuildable fallback would loop forever).
        if (
            self.hybrid_fallback is not None
            and self.hybrid_fallback not in _BUILDABLE_LOOP_TYPES
        ):
            msg = f"hybrid_fallback {self.hybrid_fallback!r} is not buildable"
            raise ValueError(msg)
        # default_loop_type must be buildable.
        if self.default_loop_type not in _BUILDABLE_LOOP_TYPES:
            msg = f"default_loop_type {self.default_loop_type!r} is not buildable"
            raise ValueError(msg)
        return self


def _match_loop_type(
    rules: tuple[AutoLoopRule, ...],
    complexity: Complexity,
    default_loop_type: str,
) -> str:
    """Find the first rule matching *complexity*, or fall back to default."""
    matched = next(
        (r.loop_type for r in rules if r.complexity == complexity),
        None,
    )
    if matched is None:
        logger.warning(
            EXECUTION_LOOP_NO_RULE_MATCH,
            complexity=complexity.value,
            fallback=default_loop_type,
            num_rules=len(rules),
        )
        return default_loop_type
    return matched


def _downgrade_for_budget(
    loop_type: str,
    budget_utilization_pct: float | None,
    budget_tight_threshold: int,
) -> str:
    """Downgrade hybrid to plan_execute when budget is tight."""
    if (
        loop_type == "hybrid"
        and budget_utilization_pct is not None
        and budget_utilization_pct >= budget_tight_threshold
    ):
        logger.info(
            EXECUTION_LOOP_BUDGET_DOWNGRADE,
            original=loop_type,
            downgraded_to="plan_execute",
            budget_utilization_pct=budget_utilization_pct,
            budget_tight_threshold=budget_tight_threshold,
        )
        return "plan_execute"
    return loop_type


def _apply_hybrid_fallback(
    loop_type: str,
    hybrid_fallback: str | None,
) -> str:
    """Replace hybrid with the configured fallback when set."""
    if loop_type == "hybrid" and hybrid_fallback is not None:
        logger.info(
            EXECUTION_LOOP_HYBRID_FALLBACK,
            fallback_to=hybrid_fallback,
        )
        return hybrid_fallback
    return loop_type


_DEFAULT_BUDGET_TIGHT_THRESHOLD: Final[int] = 80


def select_loop_type(  # noqa: PLR0913
    *,
    complexity: Complexity,
    rules: tuple[AutoLoopRule, ...],
    budget_utilization_pct: float | None = None,
    budget_tight_threshold: int = _DEFAULT_BUDGET_TIGHT_THRESHOLD,
    hybrid_fallback: str | None = None,
    default_loop_type: str = "react",
) -> str:
    """Select the execution loop type for a task.

    Applies three layers in order: rule matching, budget-aware
    downgrade, and hybrid fallback.  See ``_match_loop_type``,
    ``_downgrade_for_budget``, and ``_apply_hybrid_fallback``.

    Args:
        complexity: Task's estimated complexity.
        rules: Mapping rules from complexity to loop type.
        budget_utilization_pct: Current monthly budget utilization
            as a percentage (0--100+).  ``None`` means unknown.
        budget_tight_threshold: Percentage at or above which budget
            is considered tight.
        hybrid_fallback: Optional override when hybrid is selected.
            ``None`` preserves the hybrid selection.
        default_loop_type: Fallback loop type when no rule matches.

    Returns:
        One of ``"react"``, ``"plan_execute"``, or ``"hybrid"``,
        depending on the matched rule and active fallback/downgrade
        settings.
    """
    loop_type = _match_loop_type(rules, complexity, default_loop_type)
    loop_type = _downgrade_for_budget(
        loop_type, budget_utilization_pct, budget_tight_threshold
    )
    return _apply_hybrid_fallback(loop_type, hybrid_fallback)


def build_execution_loop(  # noqa: PLR0913
    loop_type: str,
    *,
    checkpoint_callback: CheckpointCallback | None = None,
    approval_gate: ApprovalGate | None = None,
    stagnation_detector: StagnationDetector | None = None,
    compaction_callback: CompactionCallback | None = None,
    plan_execute_config: PlanExecuteConfig | None = None,
    hybrid_loop_config: HybridLoopConfig | None = None,
) -> ExecutionLoop:
    """Build an ``ExecutionLoop`` instance from a loop type string.

    Args:
        loop_type: One of ``"react"``, ``"plan_execute"``, or
            ``"hybrid"``.
        checkpoint_callback: Optional per-turn checkpoint callback.
        approval_gate: Optional approval gate to wire into the loop.
        stagnation_detector: Optional stagnation detector.
        compaction_callback: Optional compaction callback.
        plan_execute_config: Configuration for the plan-execute loop
            (ignored when ``loop_type`` is not ``"plan_execute"``).
        hybrid_loop_config: Configuration for the hybrid loop
            (ignored when ``loop_type`` is not ``"hybrid"``).

    Returns:
        A concrete ``ExecutionLoop`` implementation.

    Raises:
        ValueError: If ``loop_type`` is not recognized.
    """
    if loop_type == "react":
        return ReactLoop(
            checkpoint_callback=checkpoint_callback,
            approval_gate=approval_gate,
            stagnation_detector=stagnation_detector,
            compaction_callback=compaction_callback,
        )
    if loop_type == "plan_execute":
        return PlanExecuteLoop(
            config=plan_execute_config,
            checkpoint_callback=checkpoint_callback,
            approval_gate=approval_gate,
            stagnation_detector=stagnation_detector,
            compaction_callback=compaction_callback,
        )
    if loop_type == "hybrid":
        return HybridLoop(
            config=hybrid_loop_config,
            checkpoint_callback=checkpoint_callback,
            approval_gate=approval_gate,
            stagnation_detector=stagnation_detector,
            compaction_callback=compaction_callback,
        )
    logger.warning(
        EXECUTION_LOOP_UNKNOWN_TYPE,
        loop_type=repr(loop_type),
        valid_types=sorted(_BUILDABLE_LOOP_TYPES),
    )
    msg = f"Unknown loop type: {loop_type!r}"
    raise ValueError(msg)
