# mypy: disable-error-code="explicit-any,explicit-override,unused-awaitable"
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
        gate=gate,
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

    async def test_pass_persists_validation_record(self) -> None:
        repo = _InMemoryRepo()
        result = _pass_result()
        applier = _applier(repo, _registry(), _Gate(result=result))

        bp = _blueprint()
        await applier.apply(_proposal(bp))

        stored = repo.rows[bp.id]
        # The gate result lives on the persisted ACTIVE row so auditors
        # can replay the apply decision without rerunning the benchmark.
        assert stored.validation == result

    async def test_apply_records_repo_save_failure(self) -> None:
        class _RaisingRepo(_InMemoryRepo):
            async def save(self, entity: ToolBlueprint) -> None:
                del entity
                msg = "simulated DB outage"
                raise RuntimeError(msg)

        repo = _RaisingRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        bp = _blueprint()
        result = await applier.apply(_proposal(bp))

        # The pending-save failure is caught per-blueprint and surfaced in
        # the ApplyResult. Because save() raised on the very first call
        # (the PENDING write), registration is never reached and the
        # registry stays empty.
        assert result.success is False
        assert result.changes_applied == 0
        assert "RuntimeError" in (result.error_message or "")
        assert registry.get_def(bp.name) is None

    async def test_apply_normalises_caller_lifecycle_fields(self) -> None:
        # The applier OWNS the lifecycle. If a caller supplies pre-set
        # validated_at / activated_at / retired_at / validation, those
        # fields must NOT survive into durable state -- only the
        # applier's own gate run can stamp them. Without this guard a
        # caller could launder fake gate evidence into the dynamic_tools
        # row and bypass the audit trail.
        repo = _InMemoryRepo()
        registry = _registry()
        gate_result = _pass_result()
        applier = _applier(repo, registry, _Gate(result=gate_result))

        leaked_validation = ToolValidationResult(
            passed=True,
            brief_passed=True,
            brief_score=10,
            baseline_score=1,
            candidate_score=2,
            margin=1,
            detail="forged",
        )
        # Pre-stamp the input with a future timestamp + a forged
        # validation record so the test can distinguish ``applier-stamped
        # _NOW`` from the laundered value if any of it survives.
        bp = _blueprint().model_copy(
            update={
                "validated_at": _NOW + timedelta(hours=1),
                "validation": leaked_validation,
            }
        )
        result = await applier.apply(_proposal(bp))

        assert result.success is True
        stored = repo.rows[bp.id]
        # The applier overwrote the laundered fields with its own
        # gate-stamped lifecycle (timestamps = FakeClock.now(),
        # validation = the gate's actual result).
        assert stored.state is ToolBlueprintState.ACTIVE
        assert stored.validated_at == _NOW
        assert stored.activated_at == _NOW
        assert stored.retired_at is None
        assert stored.validation == gate_result
        assert stored.validation != leaked_validation

    async def test_apply_rolls_back_registration_on_active_save_failure(self) -> None:
        # Registration happens BEFORE the ACTIVE-row persist; if the
        # persist fails, the live handler must be unregistered so the
        # durable state ("not in DB") matches the runtime state ("not
        # registered"). Without rollback the layered tool surface would
        # expose a tool with no audit trail.
        class _RaiseOnActiveSave(_InMemoryRepo):
            def __init__(self) -> None:
                super().__init__()
                self._saves = 0

            async def save(self, entity: ToolBlueprint) -> None:
                self._saves += 1
                if entity.state is ToolBlueprintState.ACTIVE:
                    msg = "simulated DB outage on ACTIVE save"
                    raise RuntimeError(msg)
                self.rows[entity.id] = entity

        repo = _RaiseOnActiveSave()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        bp = _blueprint()
        result = await applier.apply(_proposal(bp))

        assert result.success is False
        assert result.changes_applied == 0
        assert "RuntimeError" in (result.error_message or "")
        # Registration ran but was rolled back; durable state matches.
        assert registry.get_def(bp.name) is None

    async def test_retire_non_active_returns_false(self) -> None:
        repo = _InMemoryRepo()
        registry = _registry()
        applier = _applier(repo, registry, _Gate(result=_pass_result()))

        # A PENDING blueprint is durably stored but has never been live;
        # retire is a no-op (state stays PENDING, registry untouched).
        bp = _blueprint()
        await repo.save(bp)
        assert await applier.retire(NotBlankStr(bp.id)) is False
        assert repo.rows[bp.id].state is ToolBlueprintState.PENDING
