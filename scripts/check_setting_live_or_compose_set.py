#!/usr/bin/env python3
"""Pre-push / CI gate: a writable setting has to reach something running.

A registered setting is either fixed when a process starts or changeable while
the system runs, and there is deliberately no third category. The sibling gate
``check_setting_compose_backed.py`` enforces the first half: every
``compose_set=True`` key must actually be passed by the shipped launchers, so
the label cannot decay into "we did not wire this up".

This gate enforces the complement. A writable setting whose value reaches
nothing live is the third category wearing the first category's clothes: the
operator's write is accepted, the dashboard shows the new value, and the
behaviour does not change until somebody restarts the process. Nothing about
that failure is visible from the setting's own definition, which is how five
keys drifted into it unnoticed.

A setting passes when the scan finds it named by at least one live seam:

- a ``(namespace, key)`` pair in a settings subscriber's watched set;
- an ``enabled_by`` entry on a ``SubsystemSpec``, which the reconciler
  evaluates on every pass, or a ``settings=`` entry on a spec that also sets
  ``rebuild_on_change=True``. Without that flag the reconciler short-circuits
  on an already-active subsystem, so the write is watched but replaces
  nothing;
- a resolver read outside the construction path, in any of the shapes the tree
  uses (positional pair, ``namespace=`` / ``key=`` keywords, a bridge-config
  field bundle, a namespace-wide read, a dotted ``"ns.key"`` literal, a loop
  over a literal collection, or a helper the caller passes the namespace or
  key to);
- the namespace and key quoted together in one dashboard source file, which
  persists nothing and re-fetches through ``GET /settings``.

It fails when the only evidence sits on the construction path (inside any
module ``build_runtime_services`` reaches, or inside a subsystem activation
whose spec does not declare the key), or when there is no evidence at all.

There is deliberately no per-line opt-out. A marker here would read "this
setting is writable and reaches nothing, and that is fine", which is the
category the rule abolishes. The three sanctioned exits are: make it live, mark
it ``compose_set`` and back it in the launchers, or delete it.

Baseline: ``scripts/setting_live_or_compose_set_baseline.txt`` freezes the
pre-existing violations so the gate can ship without fixing every one in the
same change. Pass when current violations are a subset of baseline, fail
listing only the new ones, warn (but pass) on stale entries. Regenerate with
``--update-baseline`` (explicit user approval to commit the diff).

Implementation is split across sibling private modules:

- ``_setting_reachability_literals``: namespace / key literal resolution.
- ``_setting_reachability_definitions``: the registered-settings inventory.
- ``_setting_reachability_evidence``: live and construction-path evidence.
- ``_setting_reachability_helpers``: the forwarding-helper index, for a read
  whose namespace or key its caller supplies.

Usage::

    python scripts/check_setting_live_or_compose_set.py
    python scripts/check_setting_live_or_compose_set.py --repo-root /path
    python scripts/check_setting_live_or_compose_set.py --update-baseline
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import GateSourceError  # type: ignore[import-not-found]
    from _setting_reachability_definitions import (  # type: ignore[import-not-found]
        SettingScanError,
        load_definitions,
    )
    from _setting_reachability_evidence import (  # type: ignore[import-not-found]
        CONSTRUCTION,
        LIVE,
        collect_evidence,
    )
else:
    from scripts._gate_source import GateSourceError
    from scripts._setting_reachability_definitions import (
        SettingScanError,
        load_definitions,
    )
    from scripts._setting_reachability_evidence import (
        CONSTRUCTION,
        LIVE,
        collect_evidence,
    )

_BASELINE_REL: Final[str] = "scripts/setting_live_or_compose_set_baseline.txt"

# Spelled as a literal type rather than an enum because these strings are the
# baseline file's on-disk format, which must stay stable and diffable.
type Kind = Literal["unreachable", "construction-only"]

_KIND_UNREACHABLE: Final[Kind] = "unreachable"
_KIND_CONSTRUCTION: Final[Kind] = "construction-only"
_KINDS: Final[frozenset[str]] = frozenset({_KIND_UNREACHABLE, _KIND_CONSTRUCTION})
_BASELINE_HEADER: Final[str] = """\
# Frozen baseline of writable settings that reach nothing while the system
# runs. Each line is `<namespace>.<key>:<kind>`, sorted, where kind is
# `unreachable` (no seam names the setting) or `construction-only` (the only
# reads run while the runtime is assembled).
#
# scripts/check_setting_live_or_compose_set.py suppresses exactly these
# entries. A new violation, or a listed setting whose kind changes, fails the
# pre-push hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_setting_live_or_compose_set.py --update-baseline
"""

_REASONS: Final[dict[str, str]] = {
    _KIND_UNREACHABLE: (
        "writable, but no subscriber, subsystem, resolver read or dashboard"
        " reference names it"
    ),
    _KIND_CONSTRUCTION: (
        "writable, but the only reads run while the runtime is assembled, so a"
        " write applies no earlier than the next rebuild"
    ),
}


@dataclass(frozen=True)
class Violation:
    """A writable setting an operator can change without effect."""

    setting_key: str
    kind: Kind
    source_file: str
    source_line: int

    def baseline_key(self) -> str:
        """The identity a baseline row freezes."""
        return f"{self.setting_key}:{self.kind}"


def scan_repo(repo_root: Path) -> list[Violation]:
    """Return every writable setting nothing live reaches.

    Args:
        repo_root: Project root to scan.

    Returns:
        The violations, sorted by baseline key.

    Raises:
        SettingScanError: If the settings inventory cannot be resolved.
        GateSourceError: If a source file cannot be read or parsed.
    """
    definitions = load_definitions(repo_root)
    writable = [record for record in definitions if not record.compose_set]
    evidence = collect_evidence(
        repo_root, frozenset(record.pair for record in writable)
    )
    violations = [
        Violation(
            setting_key=record.setting_key,
            kind=(
                _KIND_CONSTRUCTION
                if evidence.status(record.pair) == CONSTRUCTION
                else _KIND_UNREACHABLE
            ),
            source_file=record.source_file,
            source_line=record.source_line,
        )
        for record in writable
        if evidence.status(record.pair) != LIVE
    ]
    return sorted(violations, key=Violation.baseline_key)


def run_with_baseline(
    repo_root: Path, *, baseline_path: Path
) -> tuple[list[Violation], list[str]]:
    """Split the current violations against the frozen baseline.

    Args:
        repo_root: Project root to scan.
        baseline_path: Baseline file; a missing file reads as empty.

    Returns:
        The violations absent from the baseline, and the baseline entries no
        longer violated.

    Raises:
        ValueError: If the baseline file is malformed.
        SettingScanError: If the settings inventory cannot be resolved.
        GateSourceError: If a source file cannot be read or parsed.
    """
    violations = scan_repo(repo_root)
    baseline = _load_baseline(baseline_path)
    current = {violation.baseline_key() for violation in violations}
    new = [v for v in violations if v.baseline_key() not in baseline]
    stale = sorted(baseline - current)
    return new, stale


def _load_baseline(path: Path) -> set[str]:
    """Read the baseline entries from *path*.

    Args:
        path: Baseline file; a missing file reads as empty.

    Returns:
        The frozen baseline keys.

    Raises:
        ValueError: If an entry is not ``<namespace>.<key>:<kind>``. A silently
            dropped row would suppress a violation nobody approved.
        GateSourceError: If the file exists but cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError as exc:
        message = f"{path}: baseline cannot be read ({type(exc).__name__})"
        raise GateSourceError(message) from exc
    entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _is_baseline_entry(line):
            msg = (
                f"{path.as_posix()}: malformed baseline entry {line!r};"
                " expected <namespace>.<key>:<kind> with kind one of"
                f" {sorted(_KINDS)}"
            )
            raise ValueError(msg)
        entries.add(line)
    return entries


def _is_baseline_entry(line: str) -> bool:
    """Whether *line* is a well-formed baseline row."""
    setting_key, separator, kind = line.partition(":")
    namespace, dot, key = setting_key.partition(".")
    return bool(separator and dot and namespace and key and kind in _KINDS)


def write_baseline(violations: list[Violation], path: Path) -> None:
    """Overwrite *path* with the current violation set.

    Args:
        violations: The violations to freeze.
        path: Baseline file to write.

    Raises:
        OSError: If the file cannot be written.
    """
    keys = sorted({violation.baseline_key() for violation in violations})
    body = "".join(f"{key}\n" for key in keys)
    path.write_text(_BASELINE_HEADER + body, encoding="utf-8")


def _resolve_repo_root(repo_root: Path | None) -> Path:
    """Resolve the project root, defaulting to this script's repo.

    Args:
        repo_root: Explicit root, or ``None``.

    Returns:
        The resolved root.

    Raises:
        OSError: If an explicit root does not exist.
    """
    if repo_root is not None:
        return repo_root.resolve(strict=True)
    return Path(__file__).resolve().parent.parent


def _report(new: list[Violation], stale: list[str]) -> None:
    """Print the violation and stale-entry report."""
    for violation in new:
        print(
            f"{violation.source_file}:{violation.source_line}:"
            f" {violation.setting_key} is {_REASONS[violation.kind]}"
        )
    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python"
            " scripts/check_setting_live_or_compose_set.py --update-baseline'"
            " once the fix has merged.",
            file=sys.stderr,
        )
    if new:
        print(
            f"\n{len(new)} writable setting(s) an operator can change with no"
            " effect. See docs/reference/configuration-precedence.md: make the"
            " setting apply live (a subscriber, a SubsystemSpec settings="
            " declaration, or a per-call resolver read), mark it compose_set"
            " and pass it from the launchers, or remove it. There is no"
            " per-setting opt-out.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments, or ``None`` for ``sys.argv``.

    Returns:
        Process exit code (0 pass, 1 violations, 2 the scan cannot be trusted).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root to scan. Defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"Baseline file. Defaults to {_BASELINE_REL} under the repo root.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the baseline with the current violation set.",
    )
    args = parser.parse_args(argv)

    try:
        repo_root = _resolve_repo_root(args.repo_root)
    except OSError as exc:
        print(f"--repo-root not accessible: {exc}", file=sys.stderr)
        return 2
    baseline_path = args.baseline or (repo_root / _BASELINE_REL)

    if args.update_baseline:
        return _update_baseline(repo_root, baseline_path)
    try:
        new, stale = run_with_baseline(repo_root, baseline_path=baseline_path)
    except (GateSourceError, SettingScanError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Exit 1 means "you have violations". Letting anything else reach the
        # interpreter's own handler would exit 1 too, so a broken gate would be
        # indistinguishable from a failing one to the hook that reads the code.
        print(
            f"scan failed unexpectedly ({type(exc).__name__}), so its verdict"
            " cannot be trusted",
            file=sys.stderr,
        )
        return 2
    _report(new, stale)
    return 1 if new else 0


def _update_baseline(repo_root: Path, baseline_path: Path) -> int:
    """Regenerate the baseline from the current tree.

    Args:
        repo_root: Project root to scan.
        baseline_path: Baseline file to overwrite.

    Returns:
        Process exit code (0 written, 2 the scan or the write failed).
    """
    try:
        violations = scan_repo(repo_root)
    except (GateSourceError, SettingScanError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"scan failed unexpectedly ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        write_baseline(violations, baseline_path)
    except OSError as exc:
        print(
            f"Cannot write baseline {baseline_path.as_posix()}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(
        f"Wrote {len(violations)} entries to {baseline_path.as_posix()}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
