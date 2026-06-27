"""Unit tests for the pin-validation benchmark + validator ledger.

Covers: one case per registered prompt purpose; every case passing
against the committed golden through the deterministic probe; the
validator stamping ``validated_at`` from the clock seam on a clean grade;
a mutated pin surfacing as drift; a guarded ledger-write failure logging
without flipping a clean verdict; and the behaviour-tag filter.
"""

from typing import override

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    EvalTestCase,
)
from synthorg.hr.evaluation.pin_fingerprint import load_pin_golden
from synthorg.hr.evaluation.pin_probe import (
    PIN_META_KEY,
    canonical_pin_for,
    pin_metadata_payload,
    probe_input_data,
)
from synthorg.hr.evaluation.pin_probe_runner import PinProbeRunner
from synthorg.hr.evaluation.pin_validation_benchmark import ModelPinValidationBenchmark
from synthorg.hr.evaluation.pin_validation_ledger import ModelPinValidationLedger
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.llm.model_tier_policy import tier_for_purpose
from synthorg.llm.prompt_purpose import PROMPT_PURPOSE_REGISTRY, PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_PURPOSE_COUNT = len(list(PromptPurposeId))


class _FakeRepo:
    """In-memory pin-validation repository capturing saved rows."""

    def __init__(self) -> None:
        self.saved: list[ModelPinValidationRow] = []

    async def save(self, entity: ModelPinValidationRow) -> None:
        self.saved.append(entity)

    async def get(self, entity_id: str) -> ModelPinValidationRow | None:
        for row in self.saved:
            if str(row.prompt_class_id) == str(entity_id):
                return row
        return None

    async def delete(self, entity_id: str) -> bool:
        del entity_id
        return False

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ModelPinValidationRow, ...]:
        del limit, offset
        return tuple(self.saved)


class _FailingRepo(_FakeRepo):
    """Repository whose save always fails, to exercise the guarded write."""

    @override
    async def save(self, entity: ModelPinValidationRow) -> None:
        del entity
        msg = "boom"
        raise QueryError(msg)


def _runner() -> PinProbeRunner:
    return PinProbeRunner(provider=ScriptedDriver(provider_name="test-probe"))


async def _grade_all(
    benchmark: ModelPinValidationBenchmark,
    runner: PinProbeRunner,
) -> list[tuple[EvalTestCase, BenchmarkGrade]]:
    results: list[tuple[EvalTestCase, BenchmarkGrade]] = []
    async for case in benchmark.load_test_cases():
        output = await runner.run_case(case)
        grade = await benchmark.grade(case=case, agent_output=output)
        results.append((case, grade))
    return results


async def test_one_case_per_registered_purpose() -> None:
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    cases = [case async for case in benchmark.load_test_cases()]
    assert len(cases) == _PURPOSE_COUNT
    registered = {str(p.id) for p in PROMPT_PURPOSE_REGISTRY.all_purposes()}
    assert {str(c.id) for c in cases} == registered


async def test_all_cases_pass_against_committed_golden() -> None:
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    results = await _grade_all(benchmark, _runner())
    assert len(results) == _PURPOSE_COUNT
    assert all(grade.passed for _, grade in results)
    assert all(grade.score == pytest.approx(1.0) for _, grade in results)


async def test_clean_grade_stamps_validated_at_from_clock() -> None:
    repo = _FakeRepo()
    clock = FakeClock()
    ledger = ModelPinValidationLedger(repo, clock=clock)
    benchmark = ModelPinValidationBenchmark(
        golden=dict(load_pin_golden()), ledger=ledger
    )

    await _grade_all(benchmark, _runner())

    assert len(repo.saved) == _PURPOSE_COUNT
    for row in repo.saved:
        assert row.validated_at == clock.now()
        assert row.passed is True
        assert row.tier == tier_for_purpose(row.prompt_class_id)


async def test_mutated_pin_is_drift() -> None:
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    purpose_id = PromptPurposeId.MEMORY_RERANK
    pin = canonical_pin_for(purpose_id)
    mutated = pin.model_copy(update={"max_tokens": pin.max_tokens * 2})
    case = EvalTestCase(
        id=str(purpose_id),
        behavior_tags=(BehaviorTag.VERIFICATION,),
        input_data=probe_input_data(purpose_id),
        expected_output=benchmark._golden[str(purpose_id)],
        metadata={PIN_META_KEY: pin_metadata_payload(mutated)},
    )
    output = await _runner().run_case(case)
    grade = await benchmark.grade(case=case, agent_output=output)
    assert grade.passed is False
    assert "drift" in grade.explanation


async def test_ledger_write_failure_does_not_flip_clean_grade() -> None:
    ledger = ModelPinValidationLedger(_FailingRepo(), clock=FakeClock())
    benchmark = ModelPinValidationBenchmark(
        golden=dict(load_pin_golden()), ledger=ledger
    )
    runner = _runner()
    async for case in benchmark.load_test_cases():
        output = await runner.run_case(case)
        grade = await benchmark.grade(case=case, agent_output=output)
        # The fingerprint matches the golden, so the verdict stays clean
        # even though every ledger write raises.
        assert grade.passed is True
        break


async def test_behavior_tag_filter() -> None:
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    excluded = [
        case
        async for case in benchmark.load_test_cases(
            behavior_tags=frozenset({BehaviorTag.FILE_OPERATIONS})
        )
    ]
    assert excluded == []
    included = [
        case
        async for case in benchmark.load_test_cases(
            behavior_tags=frozenset({BehaviorTag.VERIFICATION})
        )
    ]
    assert len(included) == _PURPOSE_COUNT
