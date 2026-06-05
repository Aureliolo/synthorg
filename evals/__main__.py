# module-kind: code
"""Command-line entry point for the golden-company benchmark.

Run with ``uv run python -m evals`` (see the ``benchmark`` Makefile target).
Boots the deterministic benchmark over a company config + brief suite and writes
the scorecard JSON + Markdown into ``--out-dir``. The ``--profile`` flag selects
the quality the default scripted strategy renders deliverables at, so an operator
can reproduce the competent-vs-degraded discrimination without a real provider.
"""

import argparse
from pathlib import Path

from evals.runner.profiles import BenchmarkStrategyProfile

_REPO_ROOT: Path = Path(__file__).resolve().parents[1]
_EVALS: Path = _REPO_ROOT / "evals"


def _build_parser() -> argparse.ArgumentParser:
    """Build the benchmark CLI argument parser.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Run the golden-company benchmark and emit a scorecard.",
    )
    parser.add_argument(
        "--company-config",
        type=Path,
        default=_EVALS / "baselines" / "reference.yaml",
        help="Company RootConfig YAML (default: the reference baseline).",
    )
    parser.add_argument(
        "--brief-suite",
        type=Path,
        default=_EVALS / "briefs",
        help="Directory of brief YAML files (default: evals/briefs).",
    )
    parser.add_argument(
        "--anchors-dir",
        type=Path,
        default=_EVALS / "anchors",
        help="Anchor-set directory for judged briefs (default: evals/anchors).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory the scorecard JSON + Markdown are written to.",
    )
    parser.add_argument(
        "--profile",
        type=BenchmarkStrategyProfile,
        choices=tuple(BenchmarkStrategyProfile),
        default=BenchmarkStrategyProfile.COMPETENT,
        help="Quality profile for the default scripted strategy.",
    )
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    """Run the benchmark from CLI args and print a one-line summary.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` if the scorecard passes, ``1`` otherwise.
    """
    from evals.run import run_benchmark  # noqa: PLC0415 -- deferred local import

    args = _build_parser().parse_args(argv)
    scorecard = run_benchmark(
        company_config=args.company_config,
        brief_suite=args.brief_suite,
        out_dir=args.out_dir,
        anchors_dir=args.anchors_dir,
        strategy_profile=args.profile,
    )
    verdict = "PASS" if scorecard.is_passing else "FAIL"
    print(  # noqa: T201 -- CLI summary line is the entry point's user output
        f"{verdict} {scorecard.total}/{scorecard.max_total} "
        f"profile={args.profile.value} -> {args.out_dir}"
    )
    return 0 if scorecard.is_passing else 1


if __name__ == "__main__":
    raise SystemExit(main())
