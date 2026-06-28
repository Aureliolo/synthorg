"""Unit tests for ``build_pin_validation_registry`` startup wiring.

Covers: the registry registers the pin-validation benchmark and carries
a probe runner, and runs the benchmark end-to-end (every prompt class
passing against the committed golden) even with no persistence backend
wired (the validator ledger is absent, drift checks still run).
"""

import pytest

from synthorg.api.lifecycle_helpers._model_pin_wiring import (
    build_pin_validation_registry,
)
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.persistence.state import PersistenceStateSlice
from tests._shared import make_app_state
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PURPOSE_COUNT = len(list(PromptPurposeId))


def test_registry_registers_pin_benchmark() -> None:
    registry = build_pin_validation_registry(make_app_state())
    assert "model-pin-validation" in registry.list_registered()


async def test_registry_runs_benchmark_without_backend() -> None:
    registry = build_pin_validation_registry(make_app_state())
    result = await registry.run_benchmark("model-pin-validation")
    assert result.cases_run == _PURPOSE_COUNT
    assert result.passed_count == _PURPOSE_COUNT
    assert result.average_score == pytest.approx(1.0)


async def test_registry_runs_when_backend_has_no_usable_db() -> None:
    # A wired backend whose get_db() raises (a test double, or a not-yet-
    # connected backend) must degrade like an absent backend: the benchmark
    # is still registered and drift checks run, only the stamp is skipped.
    app_state = make_app_state(
        slices={PersistenceStateSlice: {"backend": FakePersistenceBackend()}},
    )
    registry = build_pin_validation_registry(app_state)
    assert "model-pin-validation" in registry.list_registered()
    result = await registry.run_benchmark("model-pin-validation")
    assert result.passed_count == _PURPOSE_COUNT
