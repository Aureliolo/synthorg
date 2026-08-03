# module-kind: tests
"""The container's metrics reader, driven against shapes the SDK does not have.

These cover the branch the container contract test structurally cannot: that
test runs the real, current image, so it only ever sees the shape the installed
SDK actually has. The branch worth guarding is the one that fires when that
shape moves, because a silently zeroed run scores as unbeatable on tokens and
disarms the budget kill.

The module under test ships inside the OpenHands image and imports no SDK, so it
is loaded from its path rather than as a package.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_MODULE_PATH = (
    Path(__file__).resolve().parents[4] / "docker" / "openhands" / "metrics_shape.py"
)


def _load() -> ModuleType:
    """Load the in-image metrics reader by path.

    Returns:
        The freshly imported module, with its report latch empty.

    Raises:
        RuntimeError: The module could not be loaded from its path.
    """
    spec = importlib.util.spec_from_file_location("metrics_shape", _MODULE_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not load {_MODULE_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def metrics() -> ModuleType:
    """The metrics reader with a clean report latch.

    Returns:
        The loaded module.
    """
    module = _load()
    module.reset_shape_reports()
    return module


def _conversation(**usage: object) -> SimpleNamespace:
    """Build a conversation whose combined metrics carry *usage*.

    Returns:
        A stand-in shaped like the SDK's conversation.
    """
    combined = SimpleNamespace(
        accumulated_cost=1.5, accumulated_token_usage=SimpleNamespace(**usage)
    )
    return SimpleNamespace(
        conversation_stats=SimpleNamespace(get_combined_metrics=lambda: combined)
    )


class TestCurrentShape:
    def test_totals_are_read_and_flagged_intact(self, metrics: ModuleType) -> None:
        totals = metrics.totals(_conversation(prompt_tokens=120, completion_tokens=34))

        assert totals["cost"] == 1.5
        assert totals["input_tokens"] == 120
        assert totals["output_tokens"] == 34
        assert totals["metrics_shape_ok"] is True


class TestMovedShape:
    def test_a_renamed_completion_field_is_reported(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Half a rename is the dangerous case: returning the prompt count and a
        # zero completion count reads as a real, cheap measurement.
        totals = metrics.totals(_conversation(prompt_tokens=120, output_tokens=34))

        assert totals["input_tokens"] == 0
        assert totals["output_tokens"] == 0
        assert totals["metrics_shape_ok"] is False
        assert "accumulated tokens unavailable" in capsys.readouterr().err

    def test_a_renamed_prompt_field_is_reported(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        totals = metrics.totals(_conversation(completion_tokens=34))

        assert totals["metrics_shape_ok"] is False
        assert "accumulated tokens unavailable" in capsys.readouterr().err

    def test_a_missing_usage_object_is_reported(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        combined = SimpleNamespace(accumulated_cost=2.0)
        conversation = SimpleNamespace(
            conversation_stats=SimpleNamespace(get_combined_metrics=lambda: combined)
        )

        totals = metrics.totals(conversation)

        assert totals["cost"] == 2.0
        assert totals["input_tokens"] == 0
        assert totals["metrics_shape_ok"] is False
        assert "accumulated tokens unavailable" in capsys.readouterr().err

    def test_a_missing_cost_field_is_reported(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        combined = SimpleNamespace(
            accumulated_token_usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=2
            )
        )
        conversation = SimpleNamespace(
            conversation_stats=SimpleNamespace(get_combined_metrics=lambda: combined)
        )

        totals = metrics.totals(conversation)

        assert totals["cost"] == 0.0
        assert totals["metrics_shape_ok"] is False
        assert "accumulated cost unavailable" in capsys.readouterr().err

    def test_an_absent_stats_object_is_reported(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        totals = metrics.totals(SimpleNamespace())

        assert totals["cost"] == 0.0
        assert totals["input_tokens"] == 0
        assert totals["output_tokens"] == 0
        assert totals["metrics_shape_ok"] is False
        assert capsys.readouterr().err


class TestReportLatch:
    def test_each_diagnostic_is_written_once(
        self, metrics: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The read runs on every event and a moved shape stays moved, so
        # repeating it would bury the rest of the container's diagnostics.
        conversation = SimpleNamespace()
        for _ in range(5):
            metrics.totals(conversation)

        stderr = capsys.readouterr().err

        assert stderr.count("accumulated tokens unavailable") == 1
        assert stderr.count("accumulated cost unavailable") == 1
