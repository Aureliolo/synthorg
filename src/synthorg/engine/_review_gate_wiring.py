"""Boot-wiring seams for :class:`ReviewGateService`.

Post-construction setters that attach the optional completion-gate and
recording collaborators once the runtime services they depend on exist,
plus the ``has_completion_gates`` query and the background-task drain.
"""

from typing import Final, Literal

from synthorg.core.task_enums import Stakes

# Deferred to break a genuine import cycle: these collaborators live in
# engine/security packages whose graph (via engine.coordination -> budget)
# re-enters the review-gate module that imports this mixin. PEP 649 keeps
# them resolvable for typing without the runtime import.
from synthorg.engine._review_gate_receipt import DeliverableReceiptSeam
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.protocol import CompletionOracleGate
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.engine.routing_policy.config import CapabilityPolicyConfig
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.persistence.code_execution_protocol import CodeExecutionRecordRepository
from synthorg.security.redteam.protocol import RedTeamGate
from synthorg.security.visionverify.protocol import VisionVerifierGate

#: Read off the policy config's own defaults rather than restated, so the
#: unwired gate and a wired one that nobody has configured answer alike.
_UNWIRED_POLICY_CONFIG: Final[CapabilityPolicyConfig] = CapabilityPolicyConfig()


class ReviewGateWiringMixin:
    """Post-construction wiring seams for the review gate service."""

    # Slot attrs populated on the concrete ``ReviewGateService``; typed
    # as ``Any`` here because the mixin only reads/assigns them. The
    # concrete class carries the authoritative type.
    _receipt_service: DeliverableReceiptSeam | None
    _vision_gate: VisionVerifierGate | None
    _red_team_gate: RedTeamGate | None
    _deliverable_input_builder: DeliverableReviewInputBuilder | None
    _background_tasks: BackgroundTaskRegistry | None
    _red_team_on_missing_deliverable: Literal["block", "skip"]
    _capability: CapabilityPolicy | None
    _build_test_gate: BuildTestOracle | None
    _code_execution_records: CodeExecutionRecordRepository | None
    _completion_oracle_gate: CompletionOracleGate | None
    _completion_oracle_shadow_mode: bool
    _completion_oracle_min_stakes: Stakes

    def set_receipt_service(
        self, receipt_service: DeliverableReceiptSeam | None
    ) -> None:
        """Attach (or clear) the receipt service after construction.

        Passing ``None`` clears a previously-attached service so a rebuild
        does not leave the gate writing receipts through an instance whose
        collaborators were replaced underneath it.
        """
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
        :meth:`set_deliverable_input_builder`.
        """
        self._red_team_gate = red_team_gate

    def set_deliverable_input_builder(
        self, builder: DeliverableReviewInputBuilder
    ) -> None:
        """Attach the shared deliverable review-input builder (boot wiring seam).

        Wired once the flight-recorder frame store is connected, independently
        of whether the red-team gate is enabled: BOTH the completion-oracle
        peer-review gate and the red-team gate source their deliverable text +
        execution id from this one builder. Because the peer-review gate is on
        by default while red-team is opt-in, coupling this to the red-team
        subsystem would leave the oracle's reviewer with no deliverable and
        silently pass every task. Without a builder a configured gate falls
        under the ``on_missing_deliverable`` posture for every task.
        """
        self._deliverable_input_builder = builder

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

    def set_capability_policy(self, policy: CapabilityPolicy | None) -> None:
        """Attach (or clear) the one capability policy (boot wiring seam).

        The red-team threshold is read back off it per decision rather than
        copied here, so an operator's edit to ``engine.red_team_min_stakes``
        applies to the next completion with no restart: the settings
        subscriber re-points the shared instance this holds.
        """
        self._capability = policy

    @property
    def _red_team_min_stakes(self) -> Stakes:
        """Lowest task stakes whose completion must pass the red-team gate.

        Returns:
            The live threshold from the capability policy, or the shipped
            default when the policy has not been wired (a boot with no
            configured provider, where nothing dispatches anyway).
        """
        if self._capability is None:
            return _UNWIRED_POLICY_CONFIG.red_team_min_stakes
        return self._capability.config.red_team_min_stakes

    def set_build_test_gate(
        self,
        gate: BuildTestOracle | None,
        *,
        records: CodeExecutionRecordRepository | None,
    ) -> None:
        """Attach (or clear) the build/test oracle gate (boot wiring seam).

        Wired at on-startup once the persistence backend is connected, so the
        gate can read the completing task's persisted test-execution records.
        Passing ``None`` clears a previously-attached gate. When attached, the
        gate fires first in the completion chain (before the peer-review,
        red-team, and vision gates), blocking a failing / unverified code task.
        """
        self._build_test_gate = gate
        self._code_execution_records = records

    def set_completion_oracle_gate(
        self,
        gate: CompletionOracleGate | None,
        *,
        shadow_mode: bool,
        min_stakes: Stakes,
    ) -> None:
        """Attach (or clear) the agent-session peer-review gate (boot wiring seam).

        Wired at on-startup once the boot ``AgentEngine`` exists. When attached
        and the task's stakes meet ``min_stakes``, the gate dispatches an
        independent reviewer agent and reworks the task on a REJECT / ESCALATE.
        In ``shadow_mode`` the verdict is surfaced but never enforced. Passing
        ``None`` clears a previously-attached gate.
        """
        self._completion_oracle_gate = gate
        self._completion_oracle_shadow_mode = shadow_mode
        self._completion_oracle_min_stakes = min_stakes

    def has_completion_gates(self) -> bool:
        """Return whether any completion gate is configured.

        Returns:
            ``True`` when at least one completion gate (build/test,
            peer-review, red-team, or vision) is attached, so a completion
            must pass every configured gate before reaching COMPLETED. A
            ``True`` result does not imply all gates run: the chain evaluates
            only those that are attached and above their stakes threshold.
        """
        return (
            self._build_test_gate is not None
            or self._completion_oracle_gate is not None
            or self._red_team_gate is not None
            or self._vision_gate is not None
        )

    @property
    def completion_oracle_gate_attached(self) -> bool:
        """Whether the peer-review gate is attached, as distinct from wired.

        A review pipeline being present on an engine says nothing about this
        gate: it is attached only once a runtime built past the coordinator
        hands one over, and a deployment whose coordination pair is unset
        judges every completion with the build/test gate alone. A check that
        reads the pipeline would report the reviewer present in exactly that
        deployment.

        Returns:
            ``True`` when a completion-oracle gate is attached.
        """
        return self._completion_oracle_gate is not None
