"""Unit tests for ``scripts/measure_embedder_compile.py``.

Covers the pure surface only: statistics, arm selection, and rendering.
The measurement itself needs torch, sentence-transformers and (for the
CUDA arms) a GPU, none of which CI has, so the one thing these tests can
assert about the torch-touching half is the property that keeps it out of
everyone's way: the module imports with neither extra installed, because
both are imported at call time inside the runner.

Loaded through ``importlib`` against the script path, matching the
sibling gate-script tests.
"""

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

from synthorg.memory.embedding.sentence_transformer import _DEFAULT_ST_MODEL

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "measure_embedder_compile.py"


class _Arm(Protocol):
    """One compiled configuration in the script's registry."""

    name: str
    compile_kwargs: Mapping[str, object]


class _ArmResult(Protocol):
    """One arm's paired measurement."""

    arm: str
    eager_median_ms: float
    compiled_median_ms: float
    eager_p90_ms: float
    compiled_p90_ms: float
    warmup_seconds: float
    cosine: float

    @property
    def speedup(self) -> float: ...


class _ArmResultFactory(Protocol):
    """The ``ArmResult`` dataclass, as the tests construct it."""

    def __call__(
        self,
        *,
        arm: str,
        eager_median_ms: float,
        compiled_median_ms: float,
        eager_p90_ms: float,
        compiled_p90_ms: float,
        warmup_seconds: float,
        cosine: float,
    ) -> _ArmResult: ...


class _ScriptModule(Protocol):
    """The script surface these tests drive."""

    DEFAULT_MODEL: str
    CORPUS: tuple[str, ...]
    ARMS: Mapping[str, _Arm]
    ArmResult: _ArmResultFactory
    BenchmarkConfigError: type[Exception]

    @staticmethod
    def percentile(values: Sequence[float], fraction: float) -> float: ...

    @staticmethod
    def select_arms(names: Sequence[str]) -> tuple[_Arm, ...]: ...

    @staticmethod
    def render_markdown(
        results: Sequence[_ArmResult],
        *,
        device: str,
        model: str,
        torch_version: str,
    ) -> str: ...

    @staticmethod
    def render_json(
        results: Sequence[_ArmResult],
        *,
        device: str,
        model: str,
        torch_version: str,
    ) -> str: ...


def _load() -> _ScriptModule:
    """Import the script as a standalone module."""
    spec = importlib.util.spec_from_file_location(
        "measure_embedder_compile", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


def _result(
    module: _ScriptModule,
    *,
    eager_median_ms: float = 10.756,
    compiled_median_ms: float = 3.637,
    warmup_seconds: float = 10.6,
    cosine: float = 1.0,
) -> _ArmResult:
    """Build an ``ArmResult`` from the measured CUDA figures."""
    return module.ArmResult(
        arm="reduce-overhead-dynamic",
        eager_median_ms=eager_median_ms,
        compiled_median_ms=compiled_median_ms,
        eager_p90_ms=12.0,
        compiled_p90_ms=4.0,
        warmup_seconds=warmup_seconds,
        cosine=cosine,
    )


class TestImportsWithoutTheExtra:
    def test_module_imports_when_torch_is_absent(self) -> None:
        """torch and sentence-transformers must be call-time imports.

        The default install carries neither, so a module-scope import
        would make this script unloadable exactly where someone would
        first run ``--help`` to find out how to use it.
        """
        module = _load()
        assert module.DEFAULT_MODEL
        assert "torch" not in sys.modules
        assert "sentence_transformers" not in sys.modules


class TestDefaults:
    def test_default_model_is_the_shipped_constant(self) -> None:
        """The benchmark measures the configured model, never its own copy.

        Restating the identifier here would let the benchmark drift from
        what actually ships and quietly report a number for a model no
        caller uses.
        """
        module = _load()
        assert module.DEFAULT_MODEL == _DEFAULT_ST_MODEL

    def test_corpus_spans_a_range_of_token_counts(self) -> None:
        """Varying input length is the whole point of the dynamic arms.

        A fixed-length corpus would compile once and report a speedup
        that no real caller sees, since meeting titles and agendas vary.
        """
        module = _load()
        lengths = {len(text.split()) for text in module.CORPUS}
        assert len(module.CORPUS) >= 8
        assert min(lengths) == 1
        assert max(lengths) >= 12
        assert len(lengths) >= 6


class TestPercentile:
    def test_returns_the_requested_order_statistic(self) -> None:
        module = _load()
        values = [float(n) for n in range(1, 11)]
        assert module.percentile(values, 0.9) == 10.0

    def test_accepts_unsorted_input(self) -> None:
        """Callers pass raw latency samples, not sorted ones."""
        module = _load()
        assert module.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.0) == 1.0

    def test_single_sample_is_its_own_percentile(self) -> None:
        module = _load()
        assert module.percentile([7.5], 0.9) == 7.5

    def test_rejects_an_empty_sample(self) -> None:
        """An empty sample has no percentile, and 0.0 would read as fast."""
        module = _load()
        with pytest.raises(module.BenchmarkConfigError):
            module.percentile([], 0.9)


class TestSelectArms:
    def test_preserves_requested_order(self) -> None:
        module = _load()
        names = ["reduce-overhead-dynamic", "default"]
        assert [arm.name for arm in module.select_arms(names)] == names

    def test_rejects_an_unknown_arm(self) -> None:
        module = _load()
        with pytest.raises(module.BenchmarkConfigError, match="no-such-arm"):
            module.select_arms(["no-such-arm"])

    def test_every_registered_arm_is_selectable(self) -> None:
        module = _load()
        selected = module.select_arms(list(module.ARMS))
        assert len(selected) == len(module.ARMS)

    def test_registry_carries_the_upstream_configuration(self) -> None:
        """The arm that reproduces upstream's claim must stay measurable."""
        module = _load()
        arm = module.ARMS["reduce-overhead-dynamic"]
        assert arm.compile_kwargs == {"mode": "reduce-overhead", "dynamic": True}


class TestSpeedup:
    def test_is_the_ratio_of_medians(self) -> None:
        module = _load()
        result = _result(module, eager_median_ms=10.0, compiled_median_ms=5.0)
        assert result.speedup == pytest.approx(2.0)

    def test_below_one_when_compilation_loses(self) -> None:
        """A regression has to be reportable, not just an unreached branch."""
        module = _load()
        result = _result(module, eager_median_ms=10.0, compiled_median_ms=12.5)
        assert result.speedup == pytest.approx(0.8)


class TestRendering:
    def test_markdown_reports_cost_alongside_gain(self) -> None:
        """Warm-up is the price of the speedup, so it is never omitted."""
        module = _load()
        table = module.render_markdown(
            [_result(module)], device="cuda", model="m", torch_version="2.13.0"
        )
        assert "reduce-overhead-dynamic" in table
        assert "2.96" in table
        assert "10.6" in table
        assert "cuda" in table

    def test_markdown_reports_the_equivalence_check(self) -> None:
        """A speedup that changed the vectors is not a speedup."""
        module = _load()
        table = module.render_markdown(
            [_result(module, cosine=0.999964)],
            device="cuda",
            model="m",
            torch_version="2.13.0",
        )
        assert "0.999964" in table

    def test_json_is_machine_readable(self) -> None:
        module = _load()
        payload = json.loads(
            module.render_json(
                [_result(module)], device="cpu", model="m", torch_version="2.13.0"
            )
        )
        assert payload["device"] == "cpu"
        assert payload["model"] == "m"
        assert payload["results"][0]["arm"] == "reduce-overhead-dynamic"
        assert payload["results"][0]["speedup"] == pytest.approx(2.9573, rel=1e-3)
