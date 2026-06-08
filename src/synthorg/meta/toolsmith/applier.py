"""Applier that turns an approved tool-creation proposal into a live tool.

The applier is the trust boundary: a candidate blueprint is persisted as
``PENDING``, run through the benchmark gate, and only on a passing result
promoted to ``VALIDATED``/``ACTIVE``, live-registered in the dynamic
registry, and persisted (registration-then-persist so a registration
failure cannot leave a durable ACTIVE row without a live handler). A
failing gate leaves nothing registered (the blueprint keeps its
validation record for audit but never goes ``ACTIVE``).

Rollback retires an active tool: it transitions the row to ``RETIRED`` and
unregisters the live handler.
"""

import asyncio

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ApplyResult, ImprovementProposal, ProposalAltitude
from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
from synthorg.meta.toolsmith.models import ToolBlueprint, ToolBlueprintState
from synthorg.meta.toolsmith.protocol import ToolValidationGate
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_APPLY_COMPLETED,
    TOOLSMITH_APPLY_FAILED,
    TOOLSMITH_APPLY_REJECTED,
    TOOLSMITH_APPLY_STARTED,
    TOOLSMITH_BLUEPRINT_RETIRED,
)
from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
from synthorg.providers.errors import ProviderError

logger = get_logger(__name__)


class ToolCreationApplier:
    """Applies an approved ``TOOL_CREATION`` proposal.

    Args:
        repo: Durable blueprint store.
        registry: Live dynamic-tool registry.
        gate: Benchmark validation gate.
        clock: Time source for lifecycle timestamps.
    """

    def __init__(
        self,
        *,
        repo: DynamicToolRepository,
        registry: DynamicToolRegistry,
        gate: ToolValidationGate,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._gate = gate
        self._clock = clock or SystemClock()

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles tool-creation proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.TOOL_CREATION

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate then live-register each blueprint in the proposal.

        Per-blueprint failures are isolated inside the task wrapper: a
        single tool failing the gate or its persistence cannot abort the
        others. Provider-wide and system-critical exceptions still
        propagate out of the TaskGroup so the caller can fail the whole
        proposal cleanly.

        Returns:
            ``ApplyResult`` instance.
        """
        if not proposal.tool_changes:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("proposal carries no tool_changes"),
                changes_applied=0,
            )
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._apply_one_safely(blueprint))
                for blueprint in proposal.tool_changes
            ]
        applied = 0
        failures: list[str] = []
        for task in tasks:
            success, error = task.result()
            if success:
                applied += 1
            elif error is not None:
                failures.append(error)
        if failures:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("; ".join(failures)),
                changes_applied=applied,
            )
        return ApplyResult(success=True, changes_applied=applied)

    async def _apply_one_safely(
        self, blueprint: ToolBlueprint
    ) -> tuple[bool, str | None]:
        """Run ``_apply_one`` with per-blueprint error isolation.

        Returns ``(True, None)`` on a passing apply, ``(False, reason)`` on
        a gate rejection or per-blueprint exception. ``ProviderError`` and
        system-critical errors propagate so the TaskGroup surfaces them.

        Returns:
            The ``tuple[bool, str]`` value when present, ``None`` otherwise.

        Raises:
            ProviderError: Raised on the corresponding failure path.
        """
        try:
            ok = await self._apply_one(blueprint)
        except ProviderError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_APPLY_FAILED,
                tool_name=blueprint.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False, f"{blueprint.name}: {type(exc).__name__}"
        if ok:
            return True, None
        return False, f"{blueprint.name}: rejected by benchmark gate"

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate every blueprint without persisting or registering.

        Returns:
            ``ApplyResult`` instance.
        """
        if not proposal.tool_changes:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("proposal carries no tool_changes"),
                changes_applied=0,
            )
        failures: list[str] = []
        for blueprint in proposal.tool_changes:
            result = await self._gate.validate(blueprint)
            if not result.passed:
                failures.append(f"{blueprint.name}: {result.detail}")
        if failures:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("; ".join(failures)),
                changes_applied=0,
            )
        return ApplyResult(success=True, changes_applied=len(proposal.tool_changes))

    async def _apply_one(self, blueprint: ToolBlueprint) -> bool:
        """Persist, validate, and (on pass) register + activate one tool.

        Lifecycle normalisation: the applier OWNS the lifecycle. Any
        caller-supplied ``validated_at`` / ``activated_at`` / ``retired_at``
        / ``validation`` is cleared on the persisted candidate so a caller
        cannot launder fake gate evidence into durable state. The trusted
        record only forms after this applier's own gate run.

        Register-then-persist: a registration failure leaves nothing
        durably ACTIVE, and a persistence failure after a successful
        registration is rolled back by unregistering the live handler. The
        success log only fires once both sides land.

        Returns:
            ``True`` or ``False`` reflecting the condition.

        Raises:
            Exception: Raised on the corresponding failure path.
        """
        logger.info(TOOLSMITH_APPLY_STARTED, tool_name=blueprint.name)
        candidate = blueprint.model_copy(
            update={
                "state": ToolBlueprintState.PENDING,
                "validated_at": None,
                "activated_at": None,
                "retired_at": None,
                "validation": None,
            }
        )
        await self._repo.save(candidate)

        result = await self._gate.validate(candidate)
        if not result.passed:
            await self._repo.save(candidate.model_copy(update={"validation": result}))
            logger.info(
                TOOLSMITH_APPLY_REJECTED,
                tool_name=blueprint.name,
                detail=result.detail,
            )
            return False

        now = self._clock.now()
        active = candidate.model_copy(
            update={
                "state": ToolBlueprintState.ACTIVE,
                "validated_at": now,
                "activated_at": now,
                "validation": result,
            }
        )
        await self._registry.register(active)
        try:
            await self._repo.save(active)
        except Exception as exc:
            reraise_critical(exc)
            # Persisting an ACTIVE row failed: unregister the live handler
            # so the durable state ("not in DB") matches the runtime state
            # ("not registered"). Without rollback the layered tool surface
            # would expose a tool with no audit trail.
            await self._registry.unregister(active.name)
            raise
        logger.info(TOOLSMITH_APPLY_COMPLETED, tool_name=blueprint.name)
        return True

    async def retire(self, blueprint_id: NotBlankStr) -> bool:
        """Roll back an active tool: transition to RETIRED and unregister.

        Returns:
            ``True`` iff an active blueprint was retired.
        """
        existing = await self._repo.get(blueprint_id)
        if existing is None or existing.state is not ToolBlueprintState.ACTIVE:
            return False
        now = self._clock.now()
        transitioned = await self._repo.transition_if(
            blueprint_id,
            ToolBlueprintState.ACTIVE,
            ToolBlueprintState.RETIRED,
            retired_at=now,
        )
        if not transitioned:
            return False
        await self._registry.unregister(existing.name)
        logger.info(TOOLSMITH_BLUEPRINT_RETIRED, tool_name=existing.name)
        return True


__all__ = ["ToolCreationApplier"]
