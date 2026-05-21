"""Unit tests for the tool-creation applier."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
from synthorg.meta.toolsmith.models import (
    ToolBlueprint,
    ToolBlueprintState,
    ToolValidationResult,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="print('{}')",
        action_type="code:read",
        created_at=_NOW - timedelta(minutes=1),
    )


def _proposal(blueprint: ToolBlueprint) -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.TOOL_CREATION,
        title="Author slugify tool",
        description="Author a tool for the recurring textkit:slugify gap.",
        rationale=ProposalRationale(
            signal_summary="textkit:slugify requested 3x",
            pattern_detected="recurring capability gap",
            expected_impact="org can slugify",
            confidence_reasoning="recurrence threshold met",
        ),
        tool_changes=(blueprint,),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="retire_tool",
                    target=blueprint.name,
                    description="Retire the authored tool.",
                ),
            ),
            validation_check="tool is no longer registered",
        ),
        confidence=0.5,
    )


class _InMemoryRepo:
    """Minimal in-memory DynamicToolRepository for applier tests."""

    def __init__(self) -> None:
        self.rows: dict[str, ToolBlueprint] = {}

    async def save(self, entity: ToolBlueprint) -> None:
        self.rows[entity.id] = entity

    async def get(self, entity_id: str) -> ToolBlueprint | None:
        return self.rows.get(entity_id)

    async def transition_if(
        self,
        entity_id: str,
        from_state: ToolBlueprintState,
        to_state: ToolBlueprintState,
        **updates: Any,
    ) -> bool:
        row = self.rows.get(entity_id)
        if row is None or row.state is not from_state:
            return False
        self.rows[entity_id] = row.model_copy(update={"state": to_state, **updates})
        return True


class _Gate:
    def __init__(self, *, result: ToolValidationResult) -> None:
        self._result = result
        self.calls = 0

    async def validate(self, blueprint: ToolBlueprint) -> ToolValidationResult:
        del blueprint
        self.calls += 1
        return self._result


def _pass_result() -> ToolValidationResult:
    return ToolValidationResult(
        passed=True,
        brief_passed=True,
        brief_score=100,
        baseline_score=100,
        candidate_score=101,
        margin=1,
        detail="passed",
    )


def _fail_result() -> ToolValidationResult:
    return ToolValidationResult(
        passed=False,
        brief_passed=True,
        brief_score=100,
        baseline_score=100,
        candidate_score=95,
        margin=-5,
        detail="regressed",
    )


def _registry() -> DynamicToolRegistry:
    def factory(blueprint: ToolBlueprint) -> Any:
        del blueprint

        async def _handler(*, app_state: Any, arguments: Any, actor: Any = None) -> str:
            del app_state, arguments, actor
            return "{}"

        return _handler

    return DynamicToolRegistry(handler_factory=factory)


def _applier(repo: _InMemoryRepo, registry: DynamicToolRegistry, gate: _Gate) -> Any:
    return ToolCreationApplier(
        repo=repo,  # type: ignore[arg-type]
        registry=registry,
        gate=gate,  # type: ignore[arg-type]
        clock=FakeClock(start=_NOW),
    )


class TestToolCreationApplier:
    async def test_pass_activates_and_registers(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        bp = _blueprint()
        result = await applier.apply(_proposal(bp))

        assert result.success is True
        assert result.changes_applied == 1
        stored = repo.rows[bp.id]
        assert stored.state is ToolBlueprintState.ACTIVE
        assert stored.validated_at == _NOW
        assert stored.activated_at == _NOW
        assert registry.get_def(bp.name) is not None

    async def test_fail_registers_nothing(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_fail_result()))

        bp = _blueprint()
        result = await applier.apply(_proposal(bp))

        assert result.success is False
        assert result.changes_applied == 0
        stored = repo.rows[bp.id]
        assert stored.state is ToolBlueprintState.PENDING
        assert stored.validation is not None
        assert registry.get_def(bp.name) is None

    async def test_dry_run_does_not_persist(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        bp = _blueprint()
        result = await applier.dry_run(_proposal(bp))

        assert result.success is True
        assert repo.rows == {}
        assert registry.get_def(bp.name) is None

    async def test_retire_unregisters_active_tool(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        bp = _blueprint()
        await applier.apply(_proposal(bp))
        assert registry.get_def(bp.name) is not None

        retired = await applier.retire(NotBlankStr(bp.id))
        assert retired is True
        assert repo.rows[bp.id].state is ToolBlueprintState.RETIRED
        assert registry.get_def(bp.name) is None

    async def test_retire_missing_returns_false(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))
        assert await applier.retire(NotBlankStr("bp-missing")) is False

    async def test_altitude(self) -> None:
        applier = _applier(_InMemoryRepo(), _registry(), _Gate(result=_pass_result()))
        assert applier.altitude is ProposalAltitude.TOOL_CREATION
