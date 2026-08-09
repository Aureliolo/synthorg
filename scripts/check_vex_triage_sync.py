#!/usr/bin/env python3
"""Pre-push gate: the vulnerability triage ledger and what it renders agree.

``.github/vex/triage.yaml`` is the only place a vulnerability is silenced;
``.github/.trivyignore.yaml`` and ``.github/vex/synthorg.openvex.json`` are
rendered from it by ``scripts/generate_vex_documents.py``. Both are read by
tools rather than by people: the ignore file gates our own scans, and the
OpenVEX document is published as an attestation on every image and gates a
consumer's. Hand-editing either one silences a finding somewhere the ledger
does not record, which is the state this whole arrangement exists to prevent.

The gate holds three things:

* both rendered files match a fresh render of the ledger;
* the ledger satisfies its schema, so a statement cannot ship with a
  justification no consumer can interpret, or a not-affected claim with no
  product to attach it to;
* no ``re_review_by`` date has arrived. This is the replacement for Trivy's
  own ``expired_at`` and is louder on purpose: an expired suppression stops
  suppressing at the next scan, in a log nobody reads, whereas an expired
  assessment should stop the next push and be re-argued.

There is deliberately no baseline and no per-line opt-out. A ledger entry
nobody can justify today is the defect, and the fix is to re-assess it or
delete it.

Exit codes:
    0 -- the ledger and its rendered files agree, and nothing has expired.
    1 -- a violation.

Usage::

    python scripts/check_vex_triage_sync.py
"""

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from typing import Final, Protocol, cast

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_GENERATOR_PATH: Final[Path] = _REPO_ROOT / "scripts" / "generate_vex_documents.py"


class _EntryLike(Protocol):
    """The part of a ledger entry this gate reads.

    Declared structurally rather than imported: the generator is loaded by
    path at runtime, so there is no module name to import the dataclass from.

    Attributes:
        id: Vulnerability identifier.
        re_review_by: Date the assessment stops being trusted.
    """

    id: str
    re_review_by: dt.date


class _TriageLike(Protocol):
    """The part of the ledger this gate reads.

    Attributes:
        entries: Every assessed vulnerability.
    """

    entries: tuple[_EntryLike, ...]


class _GeneratorLike(Protocol):
    """The part of the generator this gate calls.

    Without it every attribute reached through the loaded module would be
    ``Any``, and the contract this gate exists to hold would be the one thing
    the type-checker never looked at.

    Attributes:
        REPO_ROOT: Root the rendered paths are reported relative to.
        REGENERATE_COMMAND: What a reader runs to fix a drifted file.
        VexTriageError: Raised when the ledger violates its schema.
    """

    REPO_ROOT: Path
    REGENERATE_COMMAND: str
    VexTriageError: type[Exception]

    def load_triage(self) -> _TriageLike: ...

    def rendered_files(self, triage: _TriageLike) -> dict[Path, str]: ...


class VexSyncError(Exception):
    """The gate could not load the generator it renders through.

    Distinct from a ledger violation: nothing about the ledger has been
    established yet, so reporting a clean tree would be a lie.
    """


def load_generator(path: Path = _GENERATOR_PATH) -> _GeneratorLike:
    """Import the generator so the gate renders through the same code.

    Args:
        path: Generator script.

    Returns:
        The imported module, narrowed to the surface this gate uses.

    Raises:
        VexSyncError: The generator could not be imported. Re-implementing its
            rendering here would drift from what actually writes the files,
            which is the one failure this gate must not have.
    """
    spec = importlib.util.spec_from_file_location("generate_vex_documents", path)
    if spec is None or spec.loader is None:
        msg = f"could not load the generator at {path}"
        raise VexSyncError(msg)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        msg = f"could not import {path}: {type(exc).__name__}: {exc}"
        raise VexSyncError(msg) from exc
    return cast("_GeneratorLike", module)


def _ledger_problems(exc: Exception) -> list[str]:
    """Unpack a ledger violation into one entry per problem.

    The generator collects every problem and joins them for its own output.
    Reading them back itemised keeps this gate's list uniform, rather than one
    pre-joined blob sitting beside individually reported drift and expiry
    entries. Reached across a path-loaded module, so the attribute is asked
    for rather than assumed.
    """
    problems = getattr(exc, "problems", None)
    if isinstance(problems, tuple):
        return [str(problem) for problem in problems]
    return [str(exc)]


def _drift_problems(generator: _GeneratorLike, triage: _TriageLike) -> list[str]:
    """Compare every rendered file against what the ledger requires."""
    problems: list[str] = []
    for path, expected in generator.rendered_files(triage).items():
        relative = path.relative_to(generator.REPO_ROOT).as_posix()
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # A generated file that is not valid UTF-8 has been corrupted, not
            # merely drifted; `UnicodeDecodeError` is a `ValueError`, so it
            # would otherwise escape as a traceback rather than a verdict.
            problems.append(f"{relative}: unreadable ({exc})")
            continue
        if actual != expected:
            problems.append(
                f"{relative}: does not match the ledger; run "
                f"`{generator.REGENERATE_COMMAND}`",
            )
    return problems


def _expiry_problems(triage: _TriageLike, today: dt.date) -> list[str]:
    """Report every entry whose re-review date has arrived."""
    return [
        f"{entry.id}: re_review_by {entry.re_review_by.isoformat()} has arrived; "
        f"re-assess the finding and update .github/vex/triage.yaml, or delete "
        f"the entry"
        for entry in triage.entries
        if entry.re_review_by <= today
    ]


def check(
    today: dt.date | None = None,
    generator: _GeneratorLike | None = None,
) -> list[str]:
    """Run the full comparison.

    Args:
        today: Date the re-review deadlines are held against; defaults to the
            current UTC date.
        generator: Pre-loaded generator module, for tests.

    Returns:
        One message per problem found, empty when the ledger is clean.

    Raises:
        VexSyncError: The generator could not be imported.
    """
    module = generator if generator is not None else load_generator()
    try:
        triage = module.load_triage()
    except module.VexTriageError as exc:
        return _ledger_problems(exc)

    problems = _drift_problems(module, triage)
    problems += _expiry_problems(triage, today or dt.datetime.now(dt.UTC).date())
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Keep the rendered Trivy ignore file and OpenVEX document "
        "matched to the triage ledger they come from.",
    )
    parser.parse_args(argv)

    try:
        problems = check()
    except VexSyncError as exc:
        print(f"FAIL (gate could not reach a verdict): {exc}", file=sys.stderr)
        return 1

    if not problems:
        return 0
    print("\nVulnerability triage ledger problems:\n", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
