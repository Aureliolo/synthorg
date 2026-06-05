"""Boot-wiring seams for :class:`ReviewGateService`.

Post-construction setters that attach the optional completion-gate and
recording collaborators once the runtime services they depend on exist,
plus the ``has_completion_gates`` query and the background-task drain.
"""

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    # Deferred to break a genuine import cycle: these collaborators live in
    # engine/security packages whose graph (via engine.coordination -> budget)
    # re-enters the review-gate module that imports this mixin. PEP 649 keeps
    # them resolvable for typing without the runtime import.
    from synthorg.engine._review_gate_receipt import DeliverableReceiptSeam
    from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
    from synthorg.observability.background_tasks import BackgroundTaskRegistry
    from synthorg.security.redteam.protocol import RedTeamGate
    from synthorg.security.visionverify.protocol import VisionVerifierGate


class ReviewGateWiringMixin:
    """Post-construction wiring seams for the review gate service."""

    # Slot attrs populated on the concrete ``ReviewGateService``; typed
    # as ``Any`` here because the mixin only reads/assigns them. The
    # concrete class carries the authoritative type.
    _receipt_service: Any
    _vision_gate: Any
    _red_team_gate: Any
    _red_team_input_builder: Any
    _background_tasks: Any
    _red_team_on_missing_deliverable: Any

    def set_receipt_service(self, receipt_service: DeliverableReceiptSeam) -> None:
        """Attach the receipt service after construction (boot wiring seam)."""
        self._receipt_service = receipt_service

    def set_vision_gate(self, vision_gate: VisionVerifierGate | None) -> None:
        """Attach (or clear) the vision gate after construction (boot wiring seam).

        Built in on-startup runtime wiring once the workspace and provider
        are available, so it is injected post-construction rather than at
        __init__. Passing ``None`` clears a previously-attached gate so an
        enabled -> disabled reinit does not leave a stale gate firing.
        """
        self._vision_gate = vision_gate

    def set_red_team_gate(self, red_team_gate: RedTeamGate | None) -> None:
        """Attach (or clear) the red-team gate after construction (boot wiring seam).

        Mirrors :meth:`set_vision_gate`: the red-team runtime is built in
        on-startup wiring once the boot ``AgentEngine`` exists, after this
        service is constructed during app construction. Once attached the
        gate fires on every path to COMPLETED (both ``complete_review``
        and ``run_pipeline``); the review input is built from the
        completing task's recorded deliverable by the
        :class:`DeliverableReviewInputBuilder` attached via
        :meth:`set_red_team_input_builder`.
        """
        self._red_team_gate = red_team_gate

    def set_red_team_input_builder(
        self, builder: DeliverableReviewInputBuilder
    ) -> None:
        """Attach the red-team review-input builder (boot wiring seam).

        Wired alongside :meth:`set_red_team_gate` once the flight-recorder
        frame store is connected. The builder sources the deliverable text
        and execution id the gate evaluates; without it a configured gate
        falls under the ``on_missing_deliverable`` posture for every task.
        """
        self._red_team_input_builder = builder

    def set_background_tasks(self, registry: BackgroundTaskRegistry) -> None:
        """Attach the background-task registry for gated completions.

        When present, ``dispatch_completion`` runs a gated approve in
        a tracked background task so the inline red-team AgentEngine
        latency does not block the operator's approve/reject response.
        """
        self._background_tasks = registry

    async def drain_background_tasks(self) -> None:
        """Drain in-flight gated-completion background tasks (shutdown seam).

        A gated approve runs the red-team evaluation in a tracked
        background task (see ``dispatch_completion``); without this
        drain a graceful shutdown would cancel an in-flight evaluation and
        leave the task stranded in IN_REVIEW. No-op when no registry is
        attached.
        """
        if self._background_tasks is not None:
            await self._background_tasks.drain()

    def set_red_team_on_missing_deliverable(
        self, policy: Literal["block", "skip"]
    ) -> None:
        """Set the red-team posture when no deliverable can be retrieved.

        Mirrors ``RedTeamConfig.on_missing_deliverable``: ``"block"``
        fails closed (a configured gate never silently passes an
        un-inspectable deliverable), ``"skip"`` allows completion.
        """
        self._red_team_on_missing_deliverable = policy

    def has_completion_gates(self) -> bool:
        """Return whether any adversarial completion gate is configured.

        Returns:
            ``True`` when at least one completion gate (red-team or
            vision) is attached, so a completion must pass every
            configured gate before reaching COMPLETED. A ``True`` result
            does not imply both gates run: the chain evaluates only those
            that are attached.
        """
        return self._red_team_gate is not None or self._vision_gate is not None
