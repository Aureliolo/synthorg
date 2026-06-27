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
from synthorg.core.types import NotBlankStr
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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.providers.drivers.scripted import ScriptedDriver
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_PURPOSE_COUNT = len(list(PromptPurposeId))


class _FakeRepo:
    """In-memory pin-validation repository matching the protocol surface."""

    def __init__(self) -> None:
        self.saved: list[ModelPinValidationRow] = []

    async def save(self, entity: ModelPinValidationRow, /) -> None:
        self.saved = [
            r for r in self.saved if r.prompt_class_id != entity.prompt_class_id
        ]
        self.saved.append(entity)

    async def get(self, entity_id: NotBlankStr, /) -> ModelPinValidationRow | None:
        for row in self.saved:
            if str(row.prompt_class_id) == str(entity_id):
                return row
        return None

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        before = len(self.saved)
        self.saved = [r for r in self.saved if str(r.prompt_class_id) != str(entity_id)]
        return len(self.saved) < before

    async def list_items(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[ModelPinValidationRow, ...]:
        del limit, offset
        return tuple(self.saved)


class _FailingRepo(_FakeRepo):
    """Repository whose save always fails, to exercise the guarded write."""

    @override
    async def save(self, entity: ModelPinValidationRow, /) -> None:
        del entity
        msg = "boom"
        raise QueryError(msg)


def _runner() -> PinProbeRunner:
    return PinProbeRunner(provider=ScriptedDriver(provider_name="test-provider"))


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
    for case, grade in results:
        assert grade.passed, f"{case.id} failed: {grade.explanation}"
        assert grade.score == pytest.approx(1.0), (
            f"{case.id} score {grade.score} != 1.0"
        )


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
        expected_output=dict(load_pin_golden())[str(purpose_id)],
        metadata={PIN_META_KEY: pin_metadata_payload(mutated)},
    )
    output = await _runner().run_case(case)
    grade = await benchmark.grade(case=case, agent_output=output)
    assert grade.passed is False
    assert "drift" in grade.explanation


async def test_case_id_pin_mismatch_fails_without_stamping() -> None:
    # A case labelled as class A but pinning class B must fail as malformed,
    # not validate A against B's golden, and must never stamp the ledger.
    repo = _FakeRepo()
    ledger = ModelPinValidationLedger(repo, clock=FakeClock())
    benchmark = ModelPinValidationBenchmark(
        golden=dict(load_pin_golden()), ledger=ledger
    )
    labelled = PromptPurposeId.MEMORY_RERANK
    other_pin = canonical_pin_for(PromptPurposeId.RESEARCH_SYNTHESIS)
    case = EvalTestCase(
        id=str(labelled),
        behavior_tags=(BehaviorTag.VERIFICATION,),
        input_data=probe_input_data(labelled),
        expected_output=dict(load_pin_golden())[str(labelled)],
        metadata={PIN_META_KEY: pin_metadata_payload(other_pin)},
    )
    output = await _runner().run_case(case)
    grade = await benchmark.grade(case=case, agent_output=output)
    assert grade.passed is False
    assert "malformed case" in grade.explanation
    assert repo.saved == []


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


async def test_absent_golden_grades_every_case_as_drift() -> None:
    # An empty golden (fresh checkout / forgotten regen) must fail every
    # case, never silently pass: expected_output is "" so no live
    # fingerprint can match.
    benchmark = ModelPinValidationBenchmark(golden={})
    results = await _grade_all(benchmark, _runner())
    assert len(results) == _PURPOSE_COUNT
    assert all(not grade.passed for _, grade in results)
    assert all("absent from golden" in grade.explanation for _, grade in results)


async def test_critical_error_in_stamp_propagates() -> None:
    # A best-effort stamp swallows persistence errors, but interpreter-
    # critical errors must still propagate (reraise_critical), never be
    # masked as a stamp-failure warning.
    class _CriticalRepo(_FakeRepo):
        @override
        async def save(self, entity: ModelPinValidationRow, /) -> None:
            del entity
            raise MemoryError

    ledger = ModelPinValidationLedger(_CriticalRepo(), clock=FakeClock())
    benchmark = ModelPinValidationBenchmark(
        golden=dict(load_pin_golden()), ledger=ledger
    )
    runner = _runner()
    async for case in benchmark.load_test_cases():
        output = await runner.run_case(case)
        with pytest.raises(MemoryError):
            await benchmark.grade(case=case, agent_output=output)
        break
