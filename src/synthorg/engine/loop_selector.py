"""Execution loop auto-selection based on task complexity.

Provides ``AutoLoopConfig`` and ``AutoLoopRule`` Pydantic models for
configuring selection rules, a pure ``select_loop_type`` function that
maps task complexity to a loop type string, and a ``build_execution_loop``
factory that instantiates the concrete loop.

Every complexity defaults to ReAct, the only loop that needs no
provisioning. Which loop actually suits which complexity is a question the
inner-loop A/B harness answers by measurement; its scoreboard is applied as
``engine.loop_complexity_overrides``, so the defaults here deliberately
express no opinion the evidence has not yet supported.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.registry import StrategyRegistry
from synthorg.core.task_enums import Complexity
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.engine.openhands.errors import OpenHandsUnavailableError
from synthorg.engine.openhands.loop import OpenHandsLoop
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.stagnation import StagnationDetector
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_NO_RULE_MATCH

logger = get_logger(__name__)


class LoopType(StrEnum):
    """The inner execution loops an agent can run.

    A closed vocabulary rather than a validated string, so a misspelled loop
    is a type error at the point it is written rather than a validation
    failure when the model is eventually constructed. Settings still store
    the plain value; :func:`resolve_loop_type` is where a stored string
    becomes a member.
    """

    REACT = "react"
    OPENHANDS = "openhands"


RETIRED_LOOP_TYPES: Final[Mapping[str, LoopType]] = MappingProxyType(
    {"plan_execute": LoopType.REACT, "hybrid": LoopType.REACT},
)
"""Loop names that shipped and no longer exist, and what runs in their place.

A settings value is validated on write and never on read, so a row written
while these names were valid outlives them and reaches this code unchanged.
``react`` is the substitute because it is the only loop that needs no
provisioning.
"""


class AutoLoopRule(BaseModel):
    """Maps a task complexity level to an execution loop type.

    Attributes:
        complexity: The task complexity this rule matches.
        loop_type: The loop that complexity runs on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    complexity: Complexity = Field(description="Task complexity level")
    loop_type: LoopType = Field(description="Loop the complexity runs on")


DEFAULT_AUTO_LOOP_RULES: tuple[AutoLoopRule, ...] = (
    AutoLoopRule(complexity=Complexity.SIMPLE, loop_type=LoopType.REACT),
    AutoLoopRule(complexity=Complexity.MEDIUM, loop_type=LoopType.REACT),
    AutoLoopRule(complexity=Complexity.COMPLEX, loop_type=LoopType.REACT),
    AutoLoopRule(complexity=Complexity.EPIC, loop_type=LoopType.REACT),
)

# Import-time completeness guard: ensures every Complexity member has a
# default rule.
_covered = {r.complexity for r in DEFAULT_AUTO_LOOP_RULES}
_all_complexities = set(Complexity)
if _covered != _all_complexities:
    _missing = _all_complexities - _covered
    msg = f"DEFAULT_AUTO_LOOP_RULES missing complexities: {_missing}"
    raise RuntimeError(msg)


def resolve_loop_type(loop_type: str) -> LoopType:
    """Resolve a configured loop name, mapping a retired one onto its substitute.

    Args:
        loop_type: A loop-type identifier read from configuration.

    Returns:
        The member the name denotes, or the substitute for a retired name.

    Raises:
        ValueError: When the name is neither current nor retired. Configuration
            that asks for a loop nobody ships is a mistake worth surfacing at
            the read, not a reason to run something else.
    """
    retired = RETIRED_LOOP_TYPES.get(loop_type)
    if retired is not None:
        return retired
    try:
        return LoopType(loop_type)
    except ValueError as exc:
        msg = (
            f"Unknown loop type {loop_type!r}; allowed: "
            f"{sorted(t.value for t in LoopType)}"
        )
        raise ValueError(msg) from exc


class AutoLoopConfig(BaseModel):
    """Configuration for automatic execution loop selection.

    Attributes:
        rules: Ordered rules mapping complexity to loop type. Each
            complexity must appear at most once.
        default_loop_type: Fallback loop type when no rule matches a
            task's complexity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rules: tuple[AutoLoopRule, ...] = Field(
        default=DEFAULT_AUTO_LOOP_RULES,
        description="Complexity-to-loop mapping rules",
    )
    default_loop_type: LoopType = Field(
        default=LoopType.REACT,
        description="Fallback loop when no rule matches a task complexity",
    )

    @model_validator(mode="after")
    def _validate_rules(self) -> Self:
        """Validate that each complexity is routed at most once.

        Returns:
            ``self`` unchanged when no complexity is named twice.

        Raises:
            ValueError: When two rules claim the same complexity, which would
                make the effective route depend on rule order.
        """
        seen: set[Complexity] = set()
        for rule in self.rules:
            if rule.complexity in seen:
                msg = f"Duplicate complexity in rules: {rule.complexity.value!r}"
                raise ValueError(msg)
            seen.add(rule.complexity)
        return self


def select_loop_type(
    *,
    complexity: Complexity,
    rules: tuple[AutoLoopRule, ...],
    default_loop_type: LoopType = LoopType.REACT,
) -> LoopType:
    """Select the execution loop type for a task.

    Args:
        complexity: Task's estimated complexity.
        rules: Mapping rules from complexity to loop type.
        default_loop_type: Fallback loop type when no rule matches.

    Returns:
        The matching rule's loop type, or ``default_loop_type`` when no
        rule covers *complexity* (with a warning log).
    """
    matched = next(
        (r.loop_type for r in rules if r.complexity == complexity),
        None,
    )
    if matched is None:
        logger.warning(
            EXECUTION_LOOP_NO_RULE_MATCH,
            complexity=complexity.value,
            fallback=default_loop_type.value,
            num_rules=len(rules),
        )
        return default_loop_type
    return matched


def _build_react_loop(
    *,
    checkpoint_callback: CheckpointCallback | None = None,
    approval_gate: ApprovalGate | None = None,
    stagnation_detector: StagnationDetector | None = None,
    compaction_callback: CompactionCallback | None = None,
    steering_inbox: SteeringInbox | None = None,
    step_classifier: StepQualityClassifier | None = None,
    **_unused: object,
) -> ExecutionLoop:
    """Build a :class:`ReactLoop` for the ``react`` strategy.

    Returns:
        A configured :class:`ReactLoop`. Unrecognised keyword arguments
        are ignored so all builders share one call signature.
    """
    return ReactLoop(
        checkpoint_callback=checkpoint_callback,
        approval_gate=approval_gate,
        stagnation_detector=stagnation_detector,
        compaction_callback=compaction_callback,
        steering_inbox=steering_inbox,
        step_classifier=step_classifier,
    )


def _build_openhands_loop(
    *,
    openhands_loop_config: OpenHandsLoopConfig | None = None,
    openhands_loop_deps: OpenHandsLoopDeps | None = None,
    **_unused: object,
) -> ExecutionLoop:
    """Build an :class:`OpenHandsLoop` for the ``openhands`` strategy.

    Returns:
        A configured :class:`OpenHandsLoop`. Unrecognised keyword arguments
        are ignored so all builders share one call signature.

    Raises:
        OpenHandsUnavailableError: If the runtime deps are not wired (the
            loop cannot reach its gateway / MCP boundaries).
    """
    if openhands_loop_deps is None:
        # The wiring pass already named the unmet piece; pointing at that
        # record beats a bare "not wired" on a failed task, which otherwise
        # sends whoever triages it hunting through unrelated boot logs.
        msg = (
            "OpenHands loop selected but its runtime deps are not wired; the "
            "most recent execution.loop.unavailable log names the missing piece"
        )
        raise OpenHandsUnavailableError(msg)
    return OpenHandsLoop(
        config=openhands_loop_config or OpenHandsLoopConfig(),
        deps=openhands_loop_deps,
    )


_LOOP_REGISTRY: StrategyRegistry[ExecutionLoop] = StrategyRegistry(
    {
        LoopType.REACT: _build_react_loop,
        LoopType.OPENHANDS: _build_openhands_loop,
    },
    kind="execution_loop",
)

# Import-time completeness guard: a member with no builder would type-check
# everywhere and fail only when a task of that complexity actually ran.
_unbuildable = {t for t in LoopType if t.value not in _LOOP_REGISTRY.names()}
if _unbuildable:
    msg = f"LoopType members with no registered builder: {_unbuildable}"
    raise RuntimeError(msg)

# Import-time completeness guard: a retired name pointing at another retired
# name would resolve in one hop to something that no longer exists.
_dead_substitutes = {k: v for k, v in RETIRED_LOOP_TYPES.items() if v not in LoopType}
if _dead_substitutes:
    msg = f"RETIRED_LOOP_TYPES substitutes that are not current: {_dead_substitutes}"
    raise RuntimeError(msg)


def registered_loop_types() -> tuple[str, ...]:
    """Return every loop type ``build_execution_loop`` can instantiate.

    Exposed so a caller can enumerate the loops rather than hardcode them: the
    A/B harness compares whatever is registered, so adding a third loop brings
    it into the comparison without touching the harness.

    Returns:
        The registered loop-type identifiers, sorted.
    """
    return _LOOP_REGISTRY.names()


def build_execution_loop(  # noqa: PLR0913
    loop_type: str,
    *,
    checkpoint_callback: CheckpointCallback | None = None,
    approval_gate: ApprovalGate | None = None,
    stagnation_detector: StagnationDetector | None = None,
    compaction_callback: CompactionCallback | None = None,
    openhands_loop_config: OpenHandsLoopConfig | None = None,
    openhands_loop_deps: OpenHandsLoopDeps | None = None,
    steering_inbox: SteeringInbox | None = None,
    step_classifier: StepQualityClassifier | None = None,
) -> ExecutionLoop:
    """Build an ``ExecutionLoop`` instance from a loop type string.

    Args:
        loop_type: Either ``"react"`` or ``"openhands"``.
        checkpoint_callback: Optional per-turn checkpoint callback.
        approval_gate: Optional approval gate to wire into the loop.
        stagnation_detector: Optional stagnation detector.
        compaction_callback: Optional compaction callback.
        openhands_loop_config: Configuration for the OpenHands loop
            (ignored when ``loop_type`` is not ``"openhands"``).
        openhands_loop_deps: Runtime deps for the OpenHands loop (the
            conversation factory, gateway signer, endpoint URLs, clock);
            required when ``loop_type`` is ``"openhands"``.
        steering_inbox: Optional steering inbox wired into the loop so it
            adopts mid-flight directives at safe boundaries.
        step_classifier: Optional step-quality classifier wired into the
            loop to score each step and surface ``quality_signals``.

    Returns:
        A concrete ``ExecutionLoop`` implementation.

    Raises:
        StrategyFactoryNotFoundError: If ``loop_type`` is not registered.
    """
    return _LOOP_REGISTRY.build(
        loop_type,
        checkpoint_callback=checkpoint_callback,
        approval_gate=approval_gate,
        stagnation_detector=stagnation_detector,
        compaction_callback=compaction_callback,
        openhands_loop_config=openhands_loop_config,
        openhands_loop_deps=openhands_loop_deps,
        steering_inbox=steering_inbox,
        step_classifier=step_classifier,
    )
