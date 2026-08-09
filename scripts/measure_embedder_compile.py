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
equally. Timing them one after the other instead lets a busy stretch land
entirely on one arm, which can invert the ranking between two arms that are
in truth within a few percent of each other.

**Warm-up is reported, never folded away.** Compilation is lazy, so the first
call through each shape pays for it. That cost is the other half of the trade:
an arm that saves a millisecond per call but spends twenty seconds compiling
needs thousands of calls per process before it breaks even.

The compiled vectors are also compared against eager, because a speedup that
quietly changes what the embedder returns is not a speedup, and an arm whose
cosine falls below ``_COSINE_FLOOR`` is reported as a failure rather than
printed as one number among many.

torch and sentence-transformers are optional extras, so both are imported at
call time; this module loads and its pure surface stays testable without them.

Usage::

    uv run python scripts/measure_embedder_compile.py --device cpu
    uv run python scripts/measure_embedder_compile.py --device cuda --format both

Exit codes:
    0 -- every arm measured and matched eager.
    2 -- bad arguments, or the optional ML extra is not installed.
    3 -- an arm's output diverged from eager beyond the cosine floor.
    Any other exception is a bug: it propagates with a traceback (exit 1)
    rather than being reclassified as one of the above.
"""

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
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

_EXIT_OK: Final[int] = 0
_EXIT_CONFIG: Final[int] = 2
_EXIT_DIVERGED: Final[int] = 3

#: Cosine below which a compiled arm is treated as having changed the
#: embedding rather than merely its latency. Measured half-precision arms sit
#: around 0.99996, so this admits precision noise and refuses anything that
#: has actually moved the vector.
_COSINE_FLOOR: Final[float] = 0.999

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

#: The entry with the most characters, which for this corpus is also the one
#: with the most words. Longest is wanted because eager and compiled kernels
#: sum in different orders, and that divergence compounds with the amount of
#: arithmetic per call, so the longest input is the most demanding equivalence
#: check available. Characters stand in for tokens to avoid loading a
#: tokeniser purely to pick a probe string.
_COSINE_PROBE: Final[str] = max(CORPUS, key=len)


class BenchmarkConfigError(Exception):
    """Raised when the requested benchmark configuration cannot be run."""


class MissingMlExtraError(Exception):
    """Raised when torch or sentence-transformers itself is not installed.

    Distinct from a plain ``ImportError`` so that an import failure raised
    from *inside* a present-but-broken dependency (a missing triton on the
    first compiled call, or a version assertion between transformers and
    tokenizers) keeps its own message instead of being reported as a missing
    extra and sending the operator to the wrong remedy.
    """


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

    Declared rather than typed as ``Any`` so the surface this depends on is
    written down in one place. The model is produced behind a ``cast``, which
    is unchecked, so an upstream signature change surfaces as a failure on the
    first ``encode`` call rather than at type-check time.
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
        warmup_seconds: Wall-clock spent on the first pass through every
            shape, paid once per process. Dominated by compilation, though it
            also contains the forward passes that trigger it.
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

    def __post_init__(self) -> None:
        """Refuse a result whose speedup could not be computed.

        Checked here rather than at the point of division so the failure names
        the arm that produced the bad measurement, instead of surfacing later
        inside a renderer with no measurement context.

        Raises:
            BenchmarkConfigError: If the compiled median is not positive.
        """
        if self.compiled_median_ms <= 0.0:
            msg = (
                f"arm {self.arm!r} reported a compiled median of "
                f"{self.compiled_median_ms}, which cannot be a latency"
            )
            raise BenchmarkConfigError(msg)

    @property
    def speedup(self) -> float:
        """How many times faster the compiled arm is than eager."""
        return self.eager_median_ms / self.compiled_median_ms

    @property
    def diverged(self) -> bool:
        """Whether compilation changed the embedding, not just its latency."""
        return self.cosine < _COSINE_FLOOR


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    """The device, model and library versions a run measured under.

    Attributes:
        device: Device the run used.
        model: Model the run measured.
        torch_version: torch version the run used.
        st_version: sentence-transformers version the run used.
    """

    device: str
    model: str
    torch_version: str
    st_version: str


def _registry(*arms: Arm) -> Mapping[str, Arm]:
    """Index *arms* by their own names.

    Derived rather than hand-keyed so a selector and the name printed in every
    result row cannot disagree.

    Returns:
        A read-only mapping from arm name to arm.
    """
    return MappingProxyType({arm.name: arm for arm in arms})


#: ``reduce-overhead`` drives CUDA graphs, so it is the arm that can reproduce
#: the large upstream numbers, and it is meaningless on CPU. The static
#: variant is kept measurable rather than dropped because its failure mode is
#: worth being able to show: with varying input lengths it compiles again for
#: each new shape until it trips the Dynamo recompile limit, then silently
#: serves eager.
ARMS: Final[Mapping[str, Arm]] = _registry(
    Arm(name="default", compile_kwargs=MappingProxyType({})),
    Arm(name="default-dynamic", compile_kwargs=MappingProxyType({"dynamic": True})),
    Arm(
        name="reduce-overhead-dynamic",
        compile_kwargs=MappingProxyType({"mode": "reduce-overhead", "dynamic": True}),
    ),
    Arm(
        name="reduce-overhead-static",
        compile_kwargs=MappingProxyType({"mode": "reduce-overhead", "dynamic": False}),
    ),
)


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


def parse_arm_names(raw: str) -> list[str]:
    """Split an ``--arms`` value into names, dropping blanks.

    Returns:
        The requested arm names, in the order given.
    """
    return [name.strip() for name in raw.split(",") if name.strip()]


def select_arms(names: Sequence[str]) -> tuple[Arm, ...]:
    """Resolve arm names to their configurations, in the requested order.

    Args:
        names: Arm selectors.

    Returns:
        The matching arms.

    Raises:
        BenchmarkConfigError: If *names* is empty, or names an arm that is not
            registered. An empty selection is refused for the same reason a
            zero round count is: it would report an empty table as a success.
    """
    if not names:
        msg = f"no arms selected; known arms: {', '.join(ARMS)}"
        raise BenchmarkConfigError(msg)
    unknown = [name for name in names if name not in ARMS]
    if unknown:
        msg = f"unknown arm(s): {', '.join(unknown)}; known arms: {', '.join(ARMS)}"
        raise BenchmarkConfigError(msg)
    return tuple(ARMS[name] for name in names)


def render_markdown(results: Sequence[ArmResult], env: RunEnvironment) -> str:
    """Render the results as a Markdown table for the docs page.

    Args:
        results: Measured arms.
        env: Device, model and library versions the run used.

    Returns:
        The rendered table, headed by the environment it describes.
    """
    header = (
        f"Model `{env.model}`, device `{env.device}`, "
        f"torch `{env.torch_version}`, "
        f"sentence-transformers `{env.st_version}`, batch size 1.\n\n"
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


def render_json(results: Sequence[ArmResult], env: RunEnvironment) -> str:
    """Render the results as JSON.

    Args:
        results: Measured arms.
        env: Device, model and library versions the run used.

    Returns:
        The rendered JSON document.
    """
    payload = {
        "device": env.device,
        "model": env.model,
        "torch": env.torch_version,
        "sentence_transformers": env.st_version,
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
                "diverged": result.diverged,
            }
            for result in results
        ],
    }
    return json.dumps(payload, indent=2)


def _torch() -> ModuleType:
    """Return the torch module.

    Returns:
        The imported module.

    Raises:
        MissingMlExtraError: If torch is not installed.
    """
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MissingMlExtraError(str(exc)) from exc
    return cast("ModuleType", torch)


def _versions() -> tuple[str, str]:
    """Return the torch and sentence-transformers versions.

    Both are recorded alongside the results because both can change the
    answer: a torch bump changes what Inductor emits, and a
    sentence-transformers bump decides whether ``encode`` routes through
    ``nn.Module.__call__`` at all, which is what makes compilation apply.

    Returns:
        The ``(torch, sentence-transformers)`` version strings.

    Raises:
        MissingMlExtraError: If either package is not installed.
    """
    try:
        import sentence_transformers
    except ImportError as exc:
        raise MissingMlExtraError(str(exc)) from exc
    return str(_torch().__version__), str(sentence_transformers.__version__)


def _synchroniser(device: str) -> Callable[[], None]:
    """Return the barrier that keeps a timed call honest on *device*.

    CUDA kernels are queued asynchronously, so a wall-clock timer around a
    bare launch would measure queuing rather than execution. ``encode``
    currently returns a host-resident array, whose device-to-host copy already
    synchronises, so this barrier is insurance rather than the only thing
    holding the measurement up: it keeps the timing correct if the encode path
    ever starts returning a device tensor.

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

    Raises:
        MissingMlExtraError: If sentence-transformers is not installed. An
            import failure raised later, from inside a dependency that IS
            installed, is deliberately left to propagate unchanged.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise MissingMlExtraError(str(exc)) from exc

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

    # Eager is warmed first and untimed, so the reported warm-up covers the
    # compiled model's first pass rather than first-touch weight allocation
    # that both arms would pay.
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


def _report(result: ArmResult) -> int:
    """Print one arm's outcome and return the exit code it implies.

    Printed per arm rather than at the end because a full run takes minutes,
    so an operator watching it should not have to wait for the table.

    Returns:
        ``_EXIT_DIVERGED`` when compilation changed the embedding, else
        ``_EXIT_OK``.
    """
    print(
        f"{result.arm}: {result.speedup:.2f}x "
        f"(warm-up {result.warmup_seconds:.1f}s, cosine {result.cosine:.6f})",
        file=sys.stderr,
    )
    if not result.diverged:
        return _EXIT_OK
    print(
        f"FAILED: {result.arm} cosine {result.cosine:.6f} is below "
        f"{_COSINE_FLOOR}; compilation changed the embedding, not just its "
        f"latency",
        file=sys.stderr,
    )
    return _EXIT_DIVERGED


def _emit(
    results: Sequence[ArmResult], *, env: RunEnvironment, output_format: str
) -> None:
    """Print whatever was measured, in the requested format(s).

    Called even when the run ended early, so a failure on a late arm does not
    discard the arms that already completed.
    """
    if not results:
        return
    if output_format in {"markdown", "both"}:
        print(render_markdown(results, env))
    if output_format in {"json", "both"}:
        print(render_json(results, env))


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
        The exit code (0 measured, 2 misconfigured or extra absent, 3 an arm
        diverged from eager).
    """
    args = _build_parser().parse_args(argv)
    try:
        rounds = _checked_rounds(args.rounds)
        arms = select_arms(parse_arm_names(args.arms))
    except BenchmarkConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_CONFIG

    results: list[ArmResult] = []
    torch_version, st_version = "unknown", "unknown"
    exit_code = _EXIT_OK
    try:
        torch_version, st_version = _versions()
        for arm in arms:
            result = measure_arm(
                model_name=args.model, device=args.device, arm=arm, rounds=rounds
            )
            results.append(result)
            exit_code = max(exit_code, _report(result))
    except MissingMlExtraError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "the ML extra is not installed; try"
            " 'uv sync --group fine-tune-cpu' (or fine-tune-gpu)",
            file=sys.stderr,
        )
        exit_code = _EXIT_CONFIG
    finally:
        env = RunEnvironment(
            device=args.device,
            model=args.model,
            torch_version=torch_version,
            st_version=st_version,
        )
        _emit(results, env=env, output_format=args.format)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
