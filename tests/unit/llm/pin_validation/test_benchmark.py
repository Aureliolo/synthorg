"""Unit tests for the pin-validation benchmark.

Covers: one case per registered prompt purpose; every case passing
against the committed golden through the deterministic probe; a mutated
pin surfacing as drift; a case whose id disagrees with its pinned class
failing as malformed; and an absent golden failing every case.
"""

import pytest

from synthorg.llm.model_pins import pin_for
from synthorg.llm.pin_validation import (
    ModelPinValidationBenchmark,
    PinGrade,
    PinProbeRunner,
    PinTestCase,
    load_pin_golden,
)
from synthorg.llm.pin_validation.probe import (
    PIN_META_KEY,
    pin_metadata_payload,
    probe_input_data,
)
from synthorg.llm.prompt_purpose import PROMPT_PURPOSE_REGISTRY, PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver

pytestmark = pytest.mark.unit

_PURPOSE_COUNT = len(list(PromptPurposeId))


def _runner() -> PinProbeRunner:
    return PinProbeRunner(provider=ScriptedDriver(provider_name="test-provider"))


async def _grade_all(
    benchmark: ModelPinValidationBenchmark,
    runner: PinProbeRunner,
) -> list[tuple[PinTestCase, PinGrade]]:
    results: list[tuple[PinTestCase, PinGrade]] = []
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


async def test_mutated_pin_is_drift() -> None:
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    purpose_id = PromptPurposeId.MEMORY_RERANK
    pin = pin_for(purpose_id)
    mutated = pin.model_copy(update={"max_tokens": pin.max_tokens * 2})
    case = PinTestCase(
        id=str(purpose_id),
        input_data=probe_input_data(purpose_id),
        expected_output=dict(load_pin_golden())[str(purpose_id)],
        metadata={PIN_META_KEY: pin_metadata_payload(mutated)},
    )
    output = await _runner().run_case(case)
    grade = await benchmark.grade(case=case, agent_output=output)
    assert grade.passed is False
    assert "drift" in grade.explanation


async def test_case_id_pin_mismatch_fails_as_malformed() -> None:
    # A case labelled as class A but pinning class B must fail as malformed,
    # not validate A against B's golden.
    benchmark = ModelPinValidationBenchmark(golden=dict(load_pin_golden()))
    labelled = PromptPurposeId.MEMORY_RERANK
    other_pin = pin_for(PromptPurposeId.RESEARCH_SYNTHESIS)
    case = PinTestCase(
        id=str(labelled),
        input_data=probe_input_data(labelled),
        expected_output=dict(load_pin_golden())[str(labelled)],
        metadata={PIN_META_KEY: pin_metadata_payload(other_pin)},
    )
    output = await _runner().run_case(case)
    grade = await benchmark.grade(case=case, agent_output=output)
    assert grade.passed is False
    assert "malformed case" in grade.explanation


async def test_absent_golden_grades_every_case_as_drift() -> None:
    # An empty golden (fresh checkout / forgotten regen) must fail every
    # case, never silently pass: expected_output is "" so no live
    # fingerprint can match.
    benchmark = ModelPinValidationBenchmark(golden={})
    results = await _grade_all(benchmark, _runner())
    assert len(results) == _PURPOSE_COUNT
    assert all(not grade.passed for _, grade in results)
    assert all("absent from golden" in grade.explanation for _, grade in results)
