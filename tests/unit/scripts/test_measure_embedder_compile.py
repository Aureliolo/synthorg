"""Unit tests for ``scripts/measure_embedder_compile.py``.

Covers everything reachable without the optional ML extras: statistics, arm
selection, rendering, result invariants, and the whole of ``main`` including
its two failure exits. Only the code that actually drives a model
(``_load_model``, ``_encode``, ``_timed``, ``measure_arm``) needs torch, and
``main`` reaches its validation and missing-extra paths long before any of
that, so those are exercised here too.

Loaded through ``importlib`` against the script path, matching the sibling
gate-script tests. The module is registered through ``monkeypatch`` so the
``sys.modules`` entry is reverted per test rather than left behind.

The measured figures used as fixture defaults come from the CUDA
reduce-overhead row recorded in ``docs/reference/embedding-evaluation.md``.
"""

import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

from synthorg.memory.embedding.sentence_transformer import _DEFAULT_ST_MODEL

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "measure_embedder_compile.py"

_EXPECTED_ARM_KWARGS: Mapping[str, Mapping[str, object]] = {
    "default": {},
    "default-dynamic": {"dynamic": True},
    "reduce-overhead-dynamic": {"mode": "reduce-overhead", "dynamic": True},
    "reduce-overhead-static": {"mode": "reduce-overhead", "dynamic": False},
}


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

    @property
    def diverged(self) -> bool: ...


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


class _RunEnvironment(Protocol):
    """The device, model and library versions a run measured under."""

    device: str
    model: str
    torch_version: str
    st_version: str


class _RunEnvironmentFactory(Protocol):
    """The ``RunEnvironment`` dataclass, as the tests construct it."""

    def __call__(
        self, *, device: str, model: str, torch_version: str, st_version: str
    ) -> _RunEnvironment: ...


class _ScriptModule(Protocol):
    """The script surface these tests drive."""

    DEFAULT_MODEL: str
    CORPUS: tuple[str, ...]
    ARMS: Mapping[str, _Arm]
    ArmResult: _ArmResultFactory
    RunEnvironment: _RunEnvironmentFactory
    BenchmarkConfigError: type[Exception]
    MissingMlExtraError: type[Exception]

    @staticmethod
    def percentile(values: Sequence[float], fraction: float) -> float: ...

    @staticmethod
    def parse_arm_names(raw: str) -> list[str]: ...

    @staticmethod
    def select_arms(names: Sequence[str]) -> tuple[_Arm, ...]: ...

    @staticmethod
    def render_markdown(results: Sequence[_ArmResult], env: _RunEnvironment) -> str: ...

    @staticmethod
    def render_json(results: Sequence[_ArmResult], env: _RunEnvironment) -> str: ...

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...

    @staticmethod
    def _checked_rounds(rounds: int) -> int: ...

    @staticmethod
    def _synchroniser(device: str) -> Callable[[], None]: ...


def _load(monkeypatch: pytest.MonkeyPatch) -> _ScriptModule:
    """Import the script as a standalone module.

    Returns:
        The loaded script module.
    """
    spec = importlib.util.spec_from_file_location(
        "measure_embedder_compile", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> _ScriptModule:
    """Return a freshly loaded script module."""
    return _load(monkeypatch)


def _result(
    module: _ScriptModule,
    *,
    arm: str = "reduce-overhead-dynamic",
    eager_median_ms: float = 10.756,
    compiled_median_ms: float = 3.637,
    eager_p90_ms: float = 12.0,
    compiled_p90_ms: float = 4.0,
    warmup_seconds: float = 10.6,
    cosine: float = 1.0,
) -> _ArmResult:
    """Build an ``ArmResult`` from the measured CUDA figures."""
    return module.ArmResult(
        arm=arm,
        eager_median_ms=eager_median_ms,
        compiled_median_ms=compiled_median_ms,
        eager_p90_ms=eager_p90_ms,
        compiled_p90_ms=compiled_p90_ms,
        warmup_seconds=warmup_seconds,
        cosine=cosine,
    )


def _env(
    module: _ScriptModule,
    *,
    device: str = "cuda",
    model: str = "m",
    torch_version: str = "2.13.0",
    st_version: str = "5.7.0",
) -> _RunEnvironment:
    """Build a ``RunEnvironment`` matching the recorded CUDA figures."""
    return module.RunEnvironment(
        device=device, model=model, torch_version=torch_version, st_version=st_version
    )


class TestImportsWithoutTheExtra:
    def test_module_imports_when_the_extras_are_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """torch and sentence-transformers must be call-time imports.

        Blocking both in ``sys.modules`` makes any module-scope import raise,
        so this holds whether or not the extras happen to be installed, rather
        than passing by accident in an environment that lacks them.
        """
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        loaded = _load(monkeypatch)
        assert loaded.DEFAULT_MODEL == _DEFAULT_ST_MODEL


class TestDefaults:
    def test_default_model_is_the_shipped_constant(self, module: _ScriptModule) -> None:
        """The benchmark measures the configured model, never its own copy.

        Restating the identifier here would let the benchmark drift from what
        actually ships and quietly report a number for a model no caller uses.
        """
        assert module.DEFAULT_MODEL == _DEFAULT_ST_MODEL

    def test_corpus_spans_a_range_of_word_counts(self, module: _ScriptModule) -> None:
        """Varying input length is the whole point of the dynamic arms.

        A fixed-length corpus would compile once and report a speedup that no
        real caller sees, since meeting titles and agendas vary. Word count
        stands in for token count so the check costs no tokeniser load.
        """
        lengths = {len(text.split()) for text in module.CORPUS}
        assert len(module.CORPUS) >= 8
        assert min(lengths) == 1
        assert max(lengths) >= 12
        assert len(lengths) >= 6


class TestPercentile:
    @pytest.mark.parametrize(
        ("values", "fraction", "expected"),
        [
            ([float(n) for n in range(1, 11)], 0.9, 10.0),
            ([5.0, 1.0, 3.0, 2.0, 4.0], 0.0, 1.0),
            ([5.0, 1.0, 3.0, 2.0, 4.0], 1.0, 5.0),
            ([7.5], 0.9, 7.5),
        ],
        ids=["order-statistic", "unsorted-min", "clamped-at-one", "single-sample"],
    )
    def test_returns_the_requested_order_statistic(
        self,
        module: _ScriptModule,
        values: list[float],
        fraction: float,
        expected: float,
    ) -> None:
        assert module.percentile(values, fraction) == expected

    def test_rejects_an_empty_sample(self, module: _ScriptModule) -> None:
        """An empty sample has no percentile, and 0.0 would read as fast."""
        with pytest.raises(module.BenchmarkConfigError):
            module.percentile([], 0.9)


class TestParseArmNames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("default,default-dynamic", ["default", "default-dynamic"]),
            (" default , default-dynamic ", ["default", "default-dynamic"]),
            ("default,,", ["default"]),
            ("", []),
            (" , ", []),
        ],
        ids=["plain", "padded", "trailing-comma", "empty", "blank-only"],
    )
    def test_splits_and_drops_blanks(
        self, module: _ScriptModule, raw: str, expected: list[str]
    ) -> None:
        assert module.parse_arm_names(raw) == expected


class TestSelectArms:
    def test_preserves_requested_order(self, module: _ScriptModule) -> None:
        names = ["reduce-overhead-dynamic", "default"]
        assert [arm.name for arm in module.select_arms(names)] == names

    def test_rejects_an_unknown_arm(self, module: _ScriptModule) -> None:
        with pytest.raises(module.BenchmarkConfigError, match="no-such-arm"):
            module.select_arms(["no-such-arm"])

    def test_rejects_an_empty_selection(self, module: _ScriptModule) -> None:
        """An empty selection would report an empty table as a success."""
        with pytest.raises(module.BenchmarkConfigError, match="no arms selected"):
            module.select_arms([])

    def test_returns_the_registered_arms_not_merely_the_right_count(
        self, module: _ScriptModule
    ) -> None:
        selected = module.select_arms(list(module.ARMS))
        assert tuple(arm.name for arm in selected) == tuple(module.ARMS)

    @pytest.mark.parametrize("name", list(_EXPECTED_ARM_KWARGS))
    def test_registry_carries_the_expected_configuration(
        self, module: _ScriptModule, name: str
    ) -> None:
        """A typo in any arm's kwargs reproduces the failure this measures.

        ``dynamic=False`` where ``True`` was meant is exactly the silent
        recompile-then-serve-eager mode the script exists to expose, and it
        would otherwise pass every other test.
        """
        assert module.ARMS[name].compile_kwargs == _EXPECTED_ARM_KWARGS[name]

    def test_registry_keys_match_their_arm_names(self, module: _ScriptModule) -> None:
        """The key selects the arm; the name is printed in every result row."""
        assert all(key == arm.name for key, arm in module.ARMS.items())


class TestArmResult:
    def test_speedup_is_the_ratio_of_medians(self, module: _ScriptModule) -> None:
        result = _result(module, eager_median_ms=10.0, compiled_median_ms=5.0)
        assert result.speedup == pytest.approx(2.0)

    def test_speedup_below_one_when_compilation_loses(
        self, module: _ScriptModule
    ) -> None:
        """A regression has to be reportable, not just an unreached branch."""
        result = _result(module, eager_median_ms=10.0, compiled_median_ms=12.5)
        assert result.speedup == pytest.approx(0.8)

    @pytest.mark.parametrize(
        ("cosine", "expected"),
        [(1.0, False), (0.999964, False), (0.9, True), (0.0, True)],
        ids=["identical", "half-precision-noise", "shifted", "degenerate"],
    )
    def test_divergence_is_judged_against_the_floor(
        self, module: _ScriptModule, cosine: float, expected: bool
    ) -> None:
        assert _result(module, cosine=cosine).diverged is expected

    def test_rejects_a_non_positive_compiled_median(
        self, module: _ScriptModule
    ) -> None:
        """Zero would divide in ``speedup``, far from where it was produced."""
        with pytest.raises(module.BenchmarkConfigError, match="cannot be a latency"):
            _result(module, compiled_median_ms=0.0)


class TestRendering:
    def test_markdown_reports_cost_alongside_gain(self, module: _ScriptModule) -> None:
        """Warm-up is the price of the speedup, so it is never omitted."""
        table = module.render_markdown([_result(module)], _env(module))
        assert "reduce-overhead-dynamic" in table
        assert "2.96" in table
        assert "10.6" in table
        assert "cuda" in table
        assert "5.7.0" in table

    def test_markdown_reports_the_equivalence_check(
        self, module: _ScriptModule
    ) -> None:
        """A speedup that changed the vectors is not a speedup."""
        table = module.render_markdown([_result(module, cosine=0.999964)], _env(module))
        assert "0.999964" in table

    def test_markdown_renders_every_row(self, module: _ScriptModule) -> None:
        """A real run always has several arms, never the single-row case."""
        table = module.render_markdown(
            [_result(module, arm="default"), _result(module, arm="default-dynamic")],
            _env(module, device="cpu"),
        )
        assert "| default |" in table
        assert "| default-dynamic |" in table

    def test_json_carries_every_field(self, module: _ScriptModule) -> None:
        payload = json.loads(
            module.render_json([_result(module)], _env(module, device="cpu"))
        )
        assert payload["device"] == "cpu"
        assert payload["model"] == "m"
        assert payload["torch"] == "2.13.0"
        assert payload["sentence_transformers"] == "5.7.0"
        assert payload["corpus_size"] == len(module.CORPUS)
        assert payload["results"] == [
            {
                "arm": "reduce-overhead-dynamic",
                "eager_median_ms": 10.756,
                "compiled_median_ms": 3.637,
                "eager_p90_ms": 12.0,
                "compiled_p90_ms": 4.0,
                "warmup_seconds": 10.6,
                "cosine": 1.0,
                "speedup": pytest.approx(2.9573, rel=1e-3),
                "diverged": False,
            }
        ]


class TestMain:
    def test_rejects_a_round_count_below_one(
        self, module: _ScriptModule, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert module.main(["--rounds", "0"]) == 2
        assert "--rounds must be at least 1" in capsys.readouterr().err

    def test_rejects_an_unknown_arm(
        self, module: _ScriptModule, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert module.main(["--arms", "no-such-arm"]) == 2
        assert "no-such-arm" in capsys.readouterr().err

    def test_rejects_an_empty_arm_selection(
        self, module: _ScriptModule, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert module.main(["--arms", ""]) == 2
        assert "no arms selected" in capsys.readouterr().err

    def test_reports_install_guidance_when_the_extra_is_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The guidance names the groups an operator actually has to install."""
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        loaded = _load(monkeypatch)
        assert loaded.main([]) == 2
        err = capsys.readouterr().err
        assert "fine-tune-cpu" in err
        assert "fine-tune-gpu" in err

    def test_renders_measured_arms(
        self,
        module: _ScriptModule,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``main`` threads the environment into both renderers."""
        monkeypatch.setattr(module, "_versions", lambda: ("2.13.0", "5.7.0"))
        monkeypatch.setattr(
            module,
            "measure_arm",
            lambda **kwargs: _result(module, arm=kwargs["arm"].name),
        )
        assert module.main(["--arms", "default", "--format", "both"]) == 0
        out = capsys.readouterr().out
        assert "| default |" in out
        assert "torch `2.13.0`" in out
        assert '"sentence_transformers": "5.7.0"' in out

    def test_reports_a_diverged_arm_as_a_failure(
        self,
        module: _ScriptModule,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A compiled arm that changed the embedding must not exit clean."""
        monkeypatch.setattr(module, "_versions", lambda: ("2.13.0", "5.7.0"))
        monkeypatch.setattr(
            module,
            "measure_arm",
            lambda **kwargs: _result(module, arm=kwargs["arm"].name, cosine=0.5),
        )
        assert module.main(["--arms", "default"]) == 3
        assert "FAILED" in capsys.readouterr().err

    def test_keeps_results_measured_before_a_failure(
        self,
        module: _ScriptModule,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A late failure must not discard the arms that already completed."""
        measured: list[str] = []

        def _fail_on_second(**kwargs: object) -> _ArmResult:
            arm = cast("_Arm", kwargs["arm"])
            if measured:
                msg = "no module named triton"
                raise module.MissingMlExtraError(msg)
            measured.append(arm.name)
            return _result(module, arm=arm.name)

        monkeypatch.setattr(module, "_versions", lambda: ("2.13.0", "5.7.0"))
        monkeypatch.setattr(module, "measure_arm", _fail_on_second)
        assert module.main(["--arms", "default,default-dynamic"]) == 2
        assert "| default |" in capsys.readouterr().out


class TestCheckedRounds:
    @pytest.mark.parametrize("rounds", [0, -1])
    def test_rejects_a_count_that_measures_nothing(
        self, module: _ScriptModule, rounds: int
    ) -> None:
        with pytest.raises(module.BenchmarkConfigError):
            module._checked_rounds(rounds)

    def test_accepts_the_minimum(self, module: _ScriptModule) -> None:
        assert module._checked_rounds(1) == 1


class TestSynchroniser:
    def test_cpu_barrier_is_a_no_op(self, module: _ScriptModule) -> None:
        """The CPU path must never reach torch."""
        assert module._synchroniser("cpu")() is None
