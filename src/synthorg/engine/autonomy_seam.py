# module-kind: code
"""The seam through which a run learns the autonomy that governs it.

Autonomy is resolved once, by one owner (the worker execution service,
which reads the per-agent level and the initiative's operator-set mode).
Before this seam existed only the solo dispatch path asked it: a
coordinated wave called ``AgentEngine.run`` directly with no autonomy at
all, so every team agent silently ran under the weakest output-scan tier
while the operator's configured posture said otherwise.

The engine holds the seam and asks it whenever a caller supplied nothing,
so the answer is the same one whichever path dispatched the run.
"""

from typing import Protocol

from synthorg.core.agent import AgentIdentity
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr


class AutonomyResolution(Protocol):
    """Resolves the effective autonomy governing one run."""

    async def __call__(
        self,
        identity: AgentIdentity,
        *,
        task_id: str,
        project_id: NotBlankStr | None = None,
    ) -> EffectiveAutonomy | None:
        """Resolve the autonomy for *identity* running *task_id*.

        Returns:
            The resolved autonomy, or ``None`` when no resolver is wired
            or resolution failed (degraded mode: the SecOps rule engine
            still governs every tool action).
        """
        ...


__all__ = ["AutonomyResolution"]
