#!/usr/bin/env python3
"""Measure ``torch.compile`` against eager on the local neural embedder.

``SentenceTransformerEmbedder.embed`` encodes a **single string** per call,
which is both where compilation can win most (launch overhead dominates a
small model at batch size 1) and where it can lose outright (a model whose
inference is mostly tokenisation and Python has little left to compile away).
Which of those holds is a property of the model and the device, not something
that can be reasoned out, so it is measured.

Two design choices carry the result, and neither is incidental:

**Arms are paired, not run in sequence.** Eager and the candidate are timed
alternately inside one loop, so any drift in machine load falls on both
equally. Measuring them one after another instead reported a 0.96x regression
for an arm that a paired run scored at 1.19x, which would have inverted the
conclusion.

**Warm-up is reported, never folded away.** Compilation is lazy, so the first
call through each shape pays for it. That cost is the other half of the trade:
an arm that saves a millisecond per call but spends twenty seconds compiling
needs tens of thousands of calls per process before it breaks even.

The compiled vectors are also compared against eager, because a speedup that
quietly changes what the embedder returns is not a speedup.

torch and sentence-transformers are optional extras, so both are imported at
call time; this module loads and its pure surface stays testable without them.

Usage::

    uv run python scripts/measure_embedder_compile.py --device cpu
    uv run python scripts/measure_embedder_compile.py --device cuda --format both

Exit codes:
    0 -- the run completed and the results were printed.
    2 -- bad arguments, or the optional ML extra is not installed.
"""

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Final, Protocol, cast

from synthorg.communication.meeting.embedder import cosine_similarity
from synthorg.memory.embedding.sentence_transformer import _DEFAULT_ST_MODEL

#: The model the shipped embedder actually loads. Read from the source of
#: truth rather than restated, so the benchmark cannot drift into reporting a
#: number for a model no caller uses.
DEFAULT_MODEL: Final[str] = _DEFAULT_ST_MODEL

_CPU: Final[str] = "cpu"
_CUDA: Final[str] = "cuda"

DEFAULT_ROUNDS: Final[int] = 10
_MIN_ROUNDS: Final[int] = 1
_P90: Final[float] = 0.9
_MS_PER_SECOND: Final[float] = 1000.0

#: Meeting titles and agendas of deliberately varied length. Length variety is
#: the point rather than realism alone: the dynamic-shape arms exist because a
#: real caller never sends one fixed token count, and a fixed-length corpus
#: would compile once and report a speedup nobody receives.
CORPUS: Final[tuple[str, ...]] = (
    "Standup",
    "Sprint planning",
    "Q3 roadmap review",
    "Decide on the auth migration approach",
    "Retrospective for the checkout release",
    "Review incident postmortem for the payment outage",
    "Align on pricing changes before the board meeting next week",
    "Discuss whether we should adopt the new vector store for agent memory",
    "Weekly sync on hiring pipeline, candidate feedback, and offer approvals",
    "Architecture review: splitting the monolith worker into per-domain queues",
    (
        "Budget planning session covering infrastructure spend, headcount, and"
        " tooling renewals for the coming year"
    ),
    "Security posture review",
    "Design critique",
    "Triage",
)

#: The longest entry, so the equivalence check runs through the most layers of
#: the model rather than through a one-token shortcut.
_COSINE_PROBE: Final[str] = max(CORPUS, key=len)


class BenchmarkConfigError(Exception):
    """Raised when the requested benchmark configuration cannot be run."""


class _Vector(Protocol):
    """The numpy-array surface the embedder's return value is read through."""

    def tolist(self) -> list[float]:
        """Return the vector's components.

        Returns:
            One float per dimension.
        """
        ...


class _EncodableModel(Protocol):
    """The sentence-transformers surface this benchmark drives.

    Declared rather than typed as ``Any`` so a signature change upstream
    surfaces here instead of silently measuring something else.
    """

    def encode(self, text: str, *, normalize_embeddings: bool = ...) -> _Vector:
        """Embed one string.

        Returns:
            The embedding vector.
        """
        ...


@dataclass(frozen=True, slots=True)
class Arm:
    """One compiled configuration, measured against eager.

    Attributes:
        name: The selector an operator passes to ``--arms``.
        compile_kwargs: Forwarded verbatim to ``nn.Module.compile``.
    """

    name: str
    compile_kwargs: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ArmResult:
    """One arm's paired measurement against eager.

    Attributes:
        arm: The arm's name.
        eager_median_ms: Median eager latency for one embed call.
        compiled_median_ms: Median compiled latency for one embed call.
        eager_p90_ms: 90th-percentile eager latency.
        compiled_p90_ms: 90th-percentile compiled latency.
        warmup_seconds: Wall-clock spent compiling, paid once per process.
        cosine: Compiled vector against the eager vector for one probe
            string. Anything below 1.0 means compilation changed what the
            embedder returns.
    """

    arm: str
    eager_median_ms: float
    compiled_median_ms: float
    eager_p90_ms: float
    compiled_p90_ms: float
    warmup_seconds: float
    cosine: float

    @property
    def speedup(self) -> float:
        """How many times faster the compiled arm is than eager."""
        return self.eager_median_ms / self.compiled_median_ms


#: ``reduce-overhead`` drives CUDA graphs, so it is the arm that can reproduce
#: the large upstream numbers, and it is meaningless on CPU. The static
#: variant is kept measurable rather than dropped because its failure mode is
#: worth being able to show: with varying input lengths it recompiles per
#: shape until it trips Dynamo's recompile limit, then silently serves eager.
ARMS: Final[Mapping[str, Arm]] = {
    "default": Arm(name="default", compile_kwargs={}),
    "default-dynamic": Arm(name="default-dynamic", compile_kwargs={"dynamic": True}),
    "reduce-overhead-dynamic": Arm(
        name="reduce-overhead-dynamic",
        compile_kwargs={"mode": "reduce-overhead", "dynamic": True},
    ),
    "reduce-overhead-static": Arm(
        name="reduce-overhead-static",
        compile_kwargs={"mode": "reduce-overhead", "dynamic": False},
    ),
}


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the order statistic *fraction* of the way through *values*.

    Args:
        values: Latency samples, in any order.
        fraction: Position in ``[0.0, 1.0]``.

    Returns:
        The sample at that position.

    Raises:
        BenchmarkConfigError: If *values* is empty. Returning 0.0 there would
            read as an infinitely fast arm.
    """
    if not values:
        msg = "cannot take a percentile of an empty sample"
        raise BenchmarkConfigError(msg)
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def select_arms(names: Sequence[str]) -> tuple[Arm, ...]:
    """Resolve arm names to their configurations, in the requested order.

    Args:
        names: Arm selectors.

    Returns:
        The matching arms.

    Raises:
        BenchmarkConfigError: If any name is not a registered arm.
    """
    unknown = [name for name in names if name not in ARMS]
    if unknown:
        msg = f"unknown arm(s): {', '.join(unknown)}; known arms: {', '.join(ARMS)}"
        raise BenchmarkConfigError(msg)
    return tuple(ARMS[name] for name in names)


def render_markdown(
    results: Sequence[ArmResult],
    *,
    device: str,
    model: str,
    torch_version: str,
) -> str:
    """Render the results as a Markdown table for the docs page.

    Args:
        results: Measured arms.
        device: Device the run used.
        model: Model the run measured.
        torch_version: torch version the run used.

    Returns:
        The rendered table, headed by the environment it describes.
    """
    header = (
        f"Model `{model}`, device `{device}`, torch `{torch_version}`, "
        f"batch size 1.\n\n"
        f"| arm | eager ms | compiled ms | speedup | warm-up s | cosine |\n"
        f"|---|---|---|---|---|---|\n"
    )
    rows = "".join(
        f"| {result.arm} | {result.eager_median_ms:.3f} "
        f"| {result.compiled_median_ms:.3f} | {result.speedup:.2f}x "
        f"| {result.warmup_seconds:.1f} | {result.cosine:.6f} |\n"
        for result in results
    )
    return header + rows


def render_json(
    results: Sequence[ArmResult],
    *,
    device: str,
    model: str,
    torch_version: str,
) -> str:
    """Render the results as JSON.

    Args:
        results: Measured arms.
        device: Device the run used.
        model: Model the run measured.
        torch_version: torch version the run used.

    Returns:
        The rendered JSON document.
    """
    payload = {
        "device": device,
        "model": model,
        "torch": torch_version,
        "corpus_size": len(CORPUS),
        "results": [
            {
                "arm": result.arm,
                "eager_median_ms": result.eager_median_ms,
                "compiled_median_ms": result.compiled_median_ms,
                "eager_p90_ms": result.eager_p90_ms,
                "compiled_p90_ms": result.compiled_p90_ms,
                "warmup_seconds": result.warmup_seconds,
                "cosine": result.cosine,
                "speedup": result.speedup,
            }
            for result in results
        ],
    }
    return json.dumps(payload, indent=2)


def _synchroniser(device: str) -> Callable[[], None]:
    """Return the barrier that makes a timed call comparable on *device*.

    CUDA kernels are queued asynchronously, so without a barrier the timer
    would measure how long it took to *launch* the work rather than to do it,
    and every compiled arm would look artificially fast.

    Returns:
        A callable that blocks until the device is idle.
    """
    if device != _CUDA:
        return lambda: None
    return cast("Callable[[], None]", _torch().cuda.synchronize)


def _load_model(
    model_name: str, device: str, compile_kwargs: Mapping[str, object] | None
) -> _EncodableModel:
    """Load the model, optionally compiled.

    Returns:
        The loaded sentence-transformers model.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device=device)
    model.eval()
    if compile_kwargs is not None:
        model.compile(**compile_kwargs)
    return cast("_EncodableModel", model)


def _encode(
    model: _EncodableModel, text: str, sync: Callable[[], None]
) -> tuple[float, ...]:
    """Embed one string exactly as ``SentenceTransformerEmbedder.embed`` does.

    Returns:
        The normalised embedding vector.
    """
    vector = model.encode(text, normalize_embeddings=True)
    sync()
    return tuple(vector.tolist())


def _timed(model: _EncodableModel, text: str, sync: Callable[[], None]) -> float:
    """Return the wall-clock cost of one embed call, in milliseconds.

    Returns:
        The elapsed milliseconds.
    """
    started = time.perf_counter()
    _encode(model, text, sync)
    return (time.perf_counter() - started) * _MS_PER_SECOND


def measure_arm(*, model_name: str, device: str, arm: Arm, rounds: int) -> ArmResult:
    """Measure one arm against eager, alternating between them.

    Args:
        model_name: Model to load for both sides of the pair.
        device: Device to run on.
        arm: The compiled configuration under test.
        rounds: Passes over the corpus.

    Returns:
        The paired measurement.
    """
    sync = _synchroniser(device)
    eager = _load_model(model_name, device, None)
    compiled = _load_model(model_name, device, arm.compile_kwargs)

    # Eager is warmed first and untimed, so the reported warm-up is the
    # compile cost alone rather than compile plus first-touch allocation.
    for text in CORPUS:
        _encode(eager, text, sync)
    started = time.perf_counter()
    for text in CORPUS:
        _encode(compiled, text, sync)
    warmup_seconds = time.perf_counter() - started

    cosine = cosine_similarity(
        _encode(eager, _COSINE_PROBE, sync),
        _encode(compiled, _COSINE_PROBE, sync),
    )

    eager_ms: list[float] = []
    compiled_ms: list[float] = []
    for _ in range(rounds):
        for text in CORPUS:
            eager_ms.append(_timed(eager, text, sync))
            compiled_ms.append(_timed(compiled, text, sync))

    return ArmResult(
        arm=arm.name,
        eager_median_ms=statistics.median(eager_ms),
        compiled_median_ms=statistics.median(compiled_ms),
        eager_p90_ms=percentile(eager_ms, _P90),
        compiled_p90_ms=percentile(compiled_ms, _P90),
        warmup_seconds=warmup_seconds,
        cosine=cosine,
    )


def _torch() -> ModuleType:
    """Return the torch module.

    Imported through one accessor so the optional dependency has a single
    call-time entry point rather than one per use site.

    Returns:
        The imported module.
    """
    import torch  # type: ignore[import-not-found]

    return cast("ModuleType", torch)


def _torch_version() -> str:
    """Return the installed torch version.

    Returns:
        The version string, recorded alongside the results because a torch
        bump is one of the two things that can change the answer.
    """
    return str(_torch().__version__)


def _checked_rounds(rounds: int) -> int:
    """Return *rounds*, refusing a count that would measure nothing.

    Returns:
        The validated round count.

    Raises:
        BenchmarkConfigError: If *rounds* is below one.
    """
    if rounds < _MIN_ROUNDS:
        msg = f"--rounds must be at least {_MIN_ROUNDS}"
        raise BenchmarkConfigError(msg)
    return rounds


def _build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        description="Measure torch.compile against eager on the local embedder."
    )
    parser.add_argument("--device", choices=(_CPU, _CUDA), default=_CPU)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help="Comma-separated arm names. Known: " + ", ".join(ARMS),
    )
    parser.add_argument(
        "--format", choices=("markdown", "json", "both"), default="markdown"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The exit code (0 measured, 2 misconfigured or extra not installed).
    """
    args = _build_parser().parse_args(argv)
    try:
        rounds = _checked_rounds(args.rounds)
        arms = select_arms(
            [name.strip() for name in args.arms.split(",") if name.strip()]
        )
    except BenchmarkConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: list[ArmResult] = []
    try:
        torch_version = _torch_version()
        for arm in arms:
            # Printed per arm rather than at the end: a full run takes
            # minutes, and a crash on a later arm would otherwise discard
            # every measurement taken before it.
            result = measure_arm(
                model_name=args.model, device=args.device, arm=arm, rounds=rounds
            )
            results.append(result)
            print(
                f"{result.arm}: {result.speedup:.2f}x "
                f"(warm-up {result.warmup_seconds:.1f}s, "
                f"cosine {result.cosine:.6f})",
                file=sys.stderr,
            )
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "the ML extra is not installed; try"
            " 'uv sync --group fine-tune-cpu' (or fine-tune-gpu)",
            file=sys.stderr,
        )
        return 2

    if args.format in {"markdown", "both"}:
        print(
            render_markdown(
                results,
                device=args.device,
                model=args.model,
                torch_version=torch_version,
            )
        )
    if args.format in {"json", "both"}:
        print(
            render_json(
                results,
                device=args.device,
                model=args.model,
                torch_version=torch_version,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
