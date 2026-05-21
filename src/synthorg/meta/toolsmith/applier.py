"""Applier that turns an approved tool-creation proposal into a live tool.

The applier is the trust boundary: a candidate blueprint is persisted as
``PENDING``, run through the benchmark gate, and only on a passing result
promoted to ``VALIDATED``/``ACTIVE``, persisted, and live-registered in the
dynamic registry. A failing gate leaves nothing registered (the blueprint
keeps its validation record for audit but never goes ``ACTIVE``).

Rollback retires an active tool: it transitions the row to ``RETIRED`` and
unregisters the live handler.
"""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ApplyResult, ImprovementProposal, ProposalAltitude
from synthorg.meta.toolsmith.models import ToolBlueprint, ToolBlueprintState
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_APPLY_COMPLETED,
    TOOLSMITH_APPLY_FAILED,
    TOOLSMITH_APPLY_REJECTED,
    TOOLSMITH_APPLY_STARTED,
    TOOLSMITH_BLUEPRINT_RETIRED,
)
from synthorg.providers.errors import ProviderError

if TYPE_CHECKING:
    from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
    from synthorg.meta.toolsmith.protocol import ToolValidationGate
    from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository

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
        """This applier handles tool-creation proposals."""
        return ProposalAltitude.TOOL_CREATION

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate then live-register each blueprint in the proposal."""
        if not proposal.tool_changes:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("proposal carries no tool_changes"),
                changes_applied=0,
            )
        applied = 0
        failures: list[str] = []
        for blueprint in proposal.tool_changes:
            try:
                ok = await self._apply_one(blueprint)
            except ProviderError:
                raise
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    TOOLSMITH_APPLY_FAILED,
                    tool_name=blueprint.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                failures.append(f"{blueprint.name}: {type(exc).__name__}")
                continue
            if ok:
                applied += 1
            else:
                failures.append(f"{blueprint.name}: rejected by benchmark gate")
        if failures:
            return ApplyResult(
                success=False,
                error_message=NotBlankStr("; ".join(failures)),
                changes_applied=applied,
            )
        return ApplyResult(success=True, changes_applied=applied)

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate every blueprint without persisting or registering."""
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
        """Persist, validate, and (on pass) activate + register one tool."""
        logger.info(TOOLSMITH_APPLY_STARTED, tool_name=blueprint.name)
        pending = blueprint.model_copy(update={"state": ToolBlueprintState.PENDING})
        await self._repo.save(pending)

        result = await self._gate.validate(blueprint)
        if not result.passed:
            await self._repo.save(blueprint.model_copy(update={"validation": result}))
            logger.info(
                TOOLSMITH_APPLY_REJECTED,
                tool_name=blueprint.name,
                detail=result.detail,
            )
            return False

        now = self._clock.now()
        active = blueprint.model_copy(
            update={
                "state": ToolBlueprintState.ACTIVE,
                "validated_at": now,
                "activated_at": now,
                "validation": result,
            }
        )
        await self._repo.save(active)
        await self._registry.register(active)
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
