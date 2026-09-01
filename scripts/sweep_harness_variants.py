"""Record the same cell under many harness settings, one after another.

The recursion-depth recorder runs ONE configuration per invocation, which is
right for it and wrong for the question "which of these settings actually
matters". Answering that means a dozen cells that differ by one flag each, and
driving those by hand is how a variant ends up differing by two.

So the matrix lives here as data. Each variant is a name and the flags that
distinguish it; everything else is identical by construction, including the
manifest, which is never edited between variants because the journal pins its
digest and an edit would make the pair read as two different matrices.

    python scripts/sweep_harness_variants.py                 # print the queue
    python scripts/sweep_harness_variants.py --record        # spend

Sequential by design. Each cell boots its own backend, its own gateway and its
own containers, and the point of the queue is to run unattended for hours
rather than to finish sooner: a machine running four recorders is one whose
results include how loaded it was. ``--concurrency`` raises it for an operator
who has measured that their machine is not the constraint.

A variant whose output directory already holds a finished REPORT is SKIPPED, so
the queue is resumable: killing it and starting it again costs nothing already
paid for, which matters when each cell is an hour of real provider spend. An
unfinished directory is handed back to the recorder, which resumes from its own
journal rather than starting the cell over.
"""

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# `evals` lives at the repository root rather than on the interpreter's path,
# and this runs as a script. The same fact is why the child gets PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.recursion_depth.emit import REPORT_JSON_NAME

#: Where each variant's journal and report are written, one directory each.
RESULTS: Final[Path] = Path("evals/recursion_depth/results")

#: The recorder this drives. Invoked as a subprocess rather than imported,
#: because a cell boots a whole backend and a failure in one variant must not
#: take the queue down with it.
RECORDER: Final[Path] = Path("scripts/record_recursion_depth.py")

#: Where the provider connections live, when nothing says otherwise. A path
#: outside the repository because it holds credentials, and one nobody's home
#: directory is baked into: an absolute path under one operator's account is
#: not a default, it is that operator's machine written down.
DEFAULT_COMPANY_CONFIG: Final[Path] = Path("providers.local.yaml")

#: The environment variable that supplies it, so a queue can be launched
#: without repeating the flag.
COMPANY_CONFIG_ENV: Final[str] = "SYNTHORG_COMPANY_CONFIG"

#: What every variant shares. Anything here that differed between variants
#: would be a second treatment nobody declared.
#: The sandbox every unit builds in and every grading runs in, BY DIGEST.
#:
#: A digest rather than the settings default, which is a published tag that
#: upstream no longer carries. A queued cell booted clean, planned, spent
#: 85,555 tokens, and died on the first container it opened with `[404] No
#: such image`, having bought a plan for a cell that could never be graded.
#: The recorder's own preflight refuses that before the first session, but a
#: queue running unattended for hours should not depend on a single check:
#: a digest cannot stop resolving on somebody else's release schedule.
SANDBOX_IMAGE: Final[str] = (
    "ghcr.io/aureliolo/synthorg-sandbox"
    "@sha256:af8996364caca94ba07b98b593a091afe4a11208d1f8c7cbe8966b35ca700e81"
)

COMMON: Final[tuple[str, ...]] = (
    "--sandbox-image",
    SANDBOX_IMAGE,
    "--work-root",
    ".recursion-depth/work",
    "--depths",
    "1",
    "--repetitions",
    "1:1",
    "--leaf-concurrency",
    "3",
    "--keep-workspaces",
)


@dataclass(frozen=True, slots=True)
class Variant:
    """One cell to record, and what makes it different from the others.

    Attributes:
        name: Names its output directory, so it is also what a report is filed
            under and what a resume matches on.
        flags: The arguments that distinguish it. Everything else comes from
            ``COMMON`` and from the committed manifest.
        why: What this variant is for, printed with the queue. A variant
            nobody can say the purpose of is one whose result nobody will be
            able to read.
    """

    name: str
    flags: tuple[str, ...]
    why: str
    repeats: int = 1


#: The matrix.
#:
#: Two axes, because two are what the evidence points at. REASONING because
#: the executor's family defaults an absent `reasoning_effort` to its most
#: expensive tier and 95-100% of every recorded session's output was thinking
#: rather than work. CONTRACT because every shared module in the recorded
#: corpus was defined by more than one unit and every one of them disagreed.
#:
#: The top tier is named `default` rather than after the tier itself, because
#: it is reached by OMITTING the parameter and there is no other way to reach
#: it: this family's dial reads low / high / max, the product's vocabulary is
#: the OpenAI one (minimal / low / medium / high), and the two overlap on two
#: values. The corpus ran there by never naming the field at all.
#:
#: Repeats because a single cell says very little here: three cap-1 cells on
#: identical inputs scored 39, 40 and 19 of 42, so one draw cannot separate a
#: treatment from a tree.
#:
#: There is deliberately NO "default reasoning, no contract" arm. That is the
#: recorded corpus, and it already has four samples (three smoke cells plus
#: `control-a`); paying for a fifth buys a control we are holding. The cells it
#: would have taken go to the SANDWICH instead, because the only published
#: ablation with numbers behind it says reasoning deeply at every phase and
#: reasoning moderately at every phase are the two arms that LOSE, and varying
#: one global tier can only ever pick between them.
MATRIX: Final[tuple[Variant, ...]] = (
    Variant(
        name="sweep-default-contract",
        flags=("--executor-reasoning-effort", "none", "--contract-stage"),
        why="the corpus's reasoning tier, with the new loop",
        repeats=2,
    ),
    Variant(
        name="sweep-sandwich-contract",
        flags=(
            "--executor-reasoning-effort",
            "high",
            "--leaf-reasoning-effort",
            "low",
            "--contract-stage",
        ),
        why="deep to plan and assemble, shallow to build: the published shape",
        repeats=2,
    ),
    Variant(
        name="sweep-high-contract",
        flags=("--executor-reasoning-effort", "high", "--contract-stage"),
        why="the shipping candidate: bounded thinking and the new loop",
        repeats=2,
    ),
    Variant(
        name="sweep-high-bare",
        flags=("--executor-reasoning-effort", "high", "--no-contract-stage"),
        why="isolates the loop at bounded thinking",
        repeats=2,
    ),
    Variant(
        name="sweep-low-contract",
        flags=("--executor-reasoning-effort", "low", "--contract-stage"),
        why="the floor: does any thinking earn its cost here",
    ),
    Variant(
        name="sweep-high-contract-1attempt",
        flags=(
            "--executor-reasoning-effort",
            "high",
            "--contract-stage",
            "--merge-attempts",
            "1",
        ),
        why="whether repair attempts buy anything once the merge can finish",
    ),
)


@dataclass
class Outcome:
    """What running one variant produced.

    Attributes:
        name: The variant's output directory name.
        status: ``recorded``, ``skipped``, or the failure's exit status.
        seconds: Wall clock the cell took.
    """

    name: str
    status: str
    seconds: float = 0.0
    log: Path | None = field(default=None)


def _queue(matrix: tuple[Variant, ...]) -> list[tuple[str, Variant]]:
    """Expand each variant's repeats into the directories they record into.

    Returns:
        Each output-directory name paired with the variant it came from.
    """
    return [
        (variant.name if variant.repeats == 1 else f"{variant.name}-r{index}", variant)
        for variant in matrix
        for index in range(variant.repeats)
    ]


def _already_recorded(out_dir: Path) -> bool:
    """Whether this variant FINISHED, which is the only reason to skip it.

    Keyed on the report rather than on the journal, and the difference decides
    what a killed queue costs. A journal exists from the recorder's first row,
    so skipping on one abandons every cell that was interrupted, permanently
    and silently: the queue reports it done and nothing ever completes it. The
    recorder resumes from its own journal, so handing an unfinished directory
    back re-buys the sessions already paid for and continues.

    Returns:
        True when a finished report is on disk.
    """
    return (out_dir / REPORT_JSON_NAME).is_file()


def _has_journal(out_dir: Path) -> bool:
    """Whether an earlier attempt at this cell left anything to continue.

    Returns:
        True when a journal is on disk, finished or not.
    """
    return out_dir.is_dir() and any(out_dir.glob("*.jsonl"))


def _run(name: str, variant: Variant, *, logs: Path, company_config: Path) -> Outcome:
    """Record one variant, capturing its output.

    Returns:
        What it produced.
    """
    out_dir = RESULTS / name
    if _already_recorded(out_dir):
        return Outcome(name=name, status="skipped")
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{name}.log"
    started = time.monotonic()
    # The recorder imports `evals`, which lives at the repository root rather
    # than on the interpreter's path, so the child needs it named. Without
    # this every cell dies at its first import and the queue reports ten
    # failures that have nothing to do with what it was measuring.
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
    with log.open("w", encoding="utf-8") as handle:
        finished = subprocess.run(
            [
                sys.executable,
                str(RECORDER),
                "--company-config",
                str(company_config),
                *COMMON,
                "--out-dir",
                str(out_dir),
                *variant.flags,
                # Only where a journal is already there. The recorder REFUSES
                # to record afresh over one rather than overwrite hours of
                # paid work, so a killed queue restarted without this reports
                # a failure per interrupted cell and never gets back to any of
                # them. Passing it unconditionally is the wrong other half:
                # a first attempt has nothing to continue.
                *(("--resume",) if _has_journal(out_dir) else ()),
                "--record",
            ],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    seconds = time.monotonic() - started
    status = "recorded" if finished.returncode == 0 else f"exit {finished.returncode}"
    return Outcome(name=name, status=status, seconds=seconds, log=log)


def main(argv: list[str] | None = None) -> int:
    """Print the queue, or work through it.

    Returns:
        0 when every variant recorded or was skipped, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        action="store_true",
        help="Actually record. Without it the queue is printed and nothing is spent.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "How many cells to record at once. One by default: each boots its "
            "own backend and containers, and a machine running several is one "
            "whose results include how loaded it was."
        ),
    )
    parser.add_argument(
        "--logs",
        type=Path,
        default=Path(".recursion-depth/sweep-logs"),
        help="Where each variant's recorder output is written.",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Record only variants whose name contains this substring.",
    )
    parser.add_argument(
        "--company-config",
        type=Path,
        default=Path(os.environ.get(COMPANY_CONFIG_ENV) or DEFAULT_COMPANY_CONFIG),
        help=(
            "The config carrying the provider connections both pairs dispatch "
            f"through. Falls back to ${COMPANY_CONFIG_ENV}, then to "
            f"{DEFAULT_COMPANY_CONFIG}."
        ),
    )
    args = parser.parse_args(argv)

    queue = [
        (name, variant)
        for name, variant in _queue(MATRIX)
        if args.only is None or args.only in name
    ]

    print(f"{len(queue)} cells queued\n")
    for name, variant in queue:
        mark = "done" if _already_recorded(RESULTS / name) else "    "
        print(f"  [{mark}] {name:34} {variant.why}")
    if not args.record:
        print("\nEach cell spends real provider tokens. Pass --record.")
        return 0

    # Checked before the queue starts, not once per cell: a missing config is
    # a mistake in the invocation, and finding it out an hour in, per cell, is
    # ten identical failures instead of one message.
    if not args.company_config.is_file():
        print(
            f"no provider config at {args.company_config}. Pass "
            f"--company-config, or set ${COMPANY_CONFIG_ENV}.",
            file=sys.stderr,
        )
        return 1

    print()
    outcomes: list[Outcome] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for outcome in pool.map(
            lambda pair: _run(
                pair[0],
                pair[1],
                logs=args.logs,
                company_config=args.company_config,
            ),
            queue,
        ):
            outcomes.append(outcome)
            print(
                f"  {outcome.status:12} {outcome.name:34} "
                f"{outcome.seconds / 60:6.1f} min"
            )

    failed = [one for one in outcomes if one.status.startswith("exit")]
    print(f"\n{len(outcomes) - len(failed)} of {len(outcomes)} cells recorded")
    for one in failed:
        print(f"  FAILED {one.name}: {one.status}, see {one.log}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
