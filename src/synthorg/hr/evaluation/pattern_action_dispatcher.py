"""Dispatch protocol for eval-loop pattern actions.

``EvalLoopCoordinator`` identifies pillar-weakness patterns and proposes
action identifiers for them. A :class:`PatternActionDispatcher` routes each
proposed action to a real remediation service (e.g. scheduling targeted
training, opening a coaching task, or raising an alert), so the loop closes
instead of stopping at a bare action identifier.

A ``@runtime_checkable`` protocol so the dispatcher is pluggable: a
deployment wires whichever concrete service routes actions, and the
coordinator stays agnostic. A coordinator built without a dispatcher
proposes and logs the action without dispatching it.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class PatternActionDispatcher(Protocol):
    """Routes a proposed eval-loop action to a remediation service."""

    async def dispatch(
        self,
        action_id: NotBlankStr,
        pattern: NotBlankStr,
    ) -> bool:
        """Dispatch ``action_id`` raised by ``pattern`` to its service.

        Args:
            action_id: The proposed action identifier (e.g.
                ``"schedule_training"``).
            pattern: The originating weakness pattern (e.g.
                ``"weakness:governance"``).

        Returns:
            ``True`` when the action was accepted by a downstream service,
            ``False`` when no handler claimed it.
        """
        ...
