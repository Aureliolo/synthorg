"""Composite evolution trigger.

OR-combines multiple triggers: fires if ANY sub-trigger fires.
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.protocols import (
    EvolutionContext,
    EvolutionTrigger,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import (
    EVOLUTION_TRIGGER_FAILED,
    EVOLUTION_TRIGGER_REQUESTED,
)

logger = get_logger(__name__)


class CompositeTrigger:
    """OR-composite of multiple evolution triggers.

    Fires if any sub-trigger returns True. Evaluates all
    triggers (no short-circuit) so that stateful triggers
    like ``PerTaskTrigger`` can update their counters.

    Args:
        triggers: Sub-triggers to combine.
    """

    def __init__(
        self,
        triggers: tuple[EvolutionTrigger, ...],
    ) -> None:
        if not triggers:
            logger.warning(
                EVOLUTION_TRIGGER_REQUESTED,
                trigger="composite",
                error="empty_triggers",
            )
            msg = "CompositeTrigger requires at least one sub-trigger"
            raise ValueError(msg)
        self._triggers = triggers

    @property
    def name(self) -> str:
        """Trigger name."""
        names = ", ".join(t.name for t in self._triggers)
        return f"composite({names})"

    async def should_trigger(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> bool:
        """OR-combine sub-triggers (evaluate all, no short-circuit).

        Returns:
            ``True`` when any sub-trigger fires; ``False`` when none
            do (sub-triggers that raise are treated as ``False`` and
            logged).
        """
        results = []
        for t in self._triggers:
            try:
                result = await t.should_trigger(
                    agent_id=agent_id,
                    context=context,
                )
            except Exception as exc:
                logger.warning(
                    EVOLUTION_TRIGGER_FAILED,
                    agent_id=str(agent_id),
                    trigger=t.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                results.append(False)
            else:
                results.append(result)
        fired = any(results)
        if fired:
            fired_names = [
                t.name for t, r in zip(self._triggers, results, strict=True) if r
            ]
            logger.debug(
                EVOLUTION_TRIGGER_REQUESTED,
                agent_id=str(agent_id),
                trigger="composite",
                fired_triggers=fired_names,
            )
        return fired
