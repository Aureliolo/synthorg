#!/usr/bin/env python3
"""Pre-push / CI gate: settings → startup wiring trace.

Detects "ghost-wired" settings -- registered in
``src/synthorg/settings/definitions/`` but consumed by a service whose
owning class is never instantiated at boot. Two known patterns surface
in the codebase today:

1. **Hardcoded-None ghost.** A service variable of form
   ``x: T | None = None`` at module scope in ``api/app.py`` (or a
   sibling lifecycle file) paired with a conditional
   ``if x is not None: x.start()`` guard. The guard always evaluates
   False, so the service never starts, and any setting whose
   consumer lives inside that service is dead at runtime even though
   the consumer code exists. Example: ``ApprovalTimeoutScheduler``.

2. **Factory-gated ghost.** A factory ``build_x(config)`` returning
   ``T | None`` whose ``None`` branch fires when a registered
   default-disabled flag is False. The lifecycle then conditions
   ``if x is not None: x.start()`` on the factory result. Example:
   ``BackupService`` gated on ``backup.enabled=False``.

Each ghost service is then matched to settings via three matchers
(first hit wins):

- **Gating-namespace match** (factory case): every setting whose
  ``namespace`` equals the gating namespace is ghost-wired.
- **Class-file containment match** (hardcoded-None case): a setting
  is ghost-wired iff its ``key`` appears as a substring in the
  source file of the ghost class AND its ``namespace`` appears in
  that file's path.
- **Pattern A direct ConfigResolver match**: the ghost class file
  contains ``ConfigResolver.get_*("<ns>", "<key>")`` matching a
  registered setting. Catches cross-namespace consumption.

Settings tagged ``read_only_post_init=True`` are skipped because
they are discoverability-only by design (the registry entry exists
so operators can introspect via ``/settings``; mutation is
rejected).

Per-line opt-out: append ``# lint-allow: bootstrap-wiring -- <reason>``
to the closing ``)`` of the ``_r.register(...)`` block. The
justification after ``--`` is required and must be non-empty
(mirrors ``# lint-allow: persistence-boundary``).

Baseline allowlist: ``scripts/setting_to_startup_trace_baseline.txt``
freezes pre-existing violations so the lint can ship without forcing
the wiring fix in the same PR. Lint behaviour: pass when current
violations ⊆ baseline; fail when new violations appear; warn (but
pass) when baseline entries are stale (fix landed). Regenerate via
``--update-baseline`` (explicit user approval to commit).

Implementation is split across sibling private modules to stay under
the 800-line per-file ceiling (CLAUDE.md):

- ``_setting_to_startup_trace_models``: dataclasses + constants.
- ``_setting_to_startup_trace_loader``: settings inventory loader.
- ``_setting_to_startup_trace_ghosts``: lifecycle parsing + ghost
  detection + scope-aware start-gate matching.
- ``_setting_to_startup_trace_violations``: violation matchers,
  Pattern A, ``scan_repo``, baseline I/O.

Usage::

    python scripts/check_setting_to_startup_trace.py
    python scripts/check_setting_to_startup_trace.py --repo-root /path/to/repo
    python scripts/check_setting_to_startup_trace.py --update-baseline
"""

import argparse
import sys
from pathlib import Path

# Sibling-import dance, mirroring scripts/check_web_design_system.py.
# Standalone CLI invocation needs the script's parent directory on
# sys.path; package-style invocation (e.g. tests loading via importlib)
# uses the ``scripts.`` prefix instead.
if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _setting_to_startup_trace_ghosts import (  # type: ignore[import-not-found]
        _build_class_index,
        _load_lifecycle_trees,
        _resolve_class_file,
        find_factory_gated_ghosts,
        find_hardcoded_none_ghosts,
    )
    from _setting_to_startup_trace_loader import (  # type: ignore[import-not-found]
        load_setting_definitions,
    )
    from _setting_to_startup_trace_models import (  # type: ignore[import-not-found]
        GhostService,
        SettingRecord,
        Violation,
    )
    from _setting_to_startup_trace_violations import (  # type: ignore[import-not-found]
        _load_baseline,
        run_with_baseline,
        scan_repo,
        write_baseline,
    )
else:
    from scripts._setting_to_startup_trace_ghosts import (
        _build_class_index,
        _load_lifecycle_trees,
        _resolve_class_file,
        find_factory_gated_ghosts,
        find_hardcoded_none_ghosts,
    )
    from scripts._setting_to_startup_trace_loader import load_setting_definitions
    from scripts._setting_to_startup_trace_models import (
        GhostService,
        SettingRecord,
        Violation,
    )
    from scripts._setting_to_startup_trace_violations import (
        _load_baseline,
        run_with_baseline,
        scan_repo,
        write_baseline,
    )

# Re-exports for the test importlib-loader pattern. Tests load this
# script via ``importlib.util.spec_from_file_location`` and access
# names through the module object; the explicit ``__all__`` below is
# documentation, not enforcement, but keeps the contract visible.
__all__ = (
    "GhostService",
    "SettingRecord",
    "Violation",
    "_build_class_index",
    "_load_baseline",
    "_load_lifecycle_trees",
    "_resolve_class_file",
    "find_factory_gated_ghosts",
    "find_hardcoded_none_ghosts",
    "load_setting_definitions",
    "main",
    "run_with_baseline",
    "scan_repo",
    "write_baseline",
)


def _format_violation_line(v: Violation) -> str:
    """One-line stdout violation report."""
    return (
        f"{v.source_file}:{v.source_line}: setting {v.setting_key} is "
        f"{v.kind} -- {v.reason}"
    )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root, defaulting to this script's repo."""
    if repo_root is not None:
        return repo_root.resolve(strict=True)
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to scan. Defaults to the script's repo "
            "(parent of scripts/). Pass ${{ github.workspace }} in CI "
            "to remove ambiguity."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file. Defaults to "
            "scripts/setting_to_startup_trace_baseline.txt under the "
            "resolved repo root."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Overwrite the baseline file with the current violation set "
            "(commit the diff after manual review)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except OSError as exc:
        print(f"--repo-root not accessible: {exc}", file=sys.stderr)
        return 2

    baseline_path = args.baseline or (
        project_root / "scripts" / "setting_to_startup_trace_baseline.txt"
    )

    if args.update_baseline:
        try:
            violations = scan_repo(project_root, baseline_path=None)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
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

    try:
        new, stale = run_with_baseline(project_root, baseline_path=baseline_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for v in new:
        print(_format_violation_line(v))

    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python scripts/check_setting_to_startup_trace.py "
            "--update-baseline' once the wiring fix has merged.",
            file=sys.stderr,
        )

    if new:
        print(
            f"\n{len(new)} new ghost-wired setting(s). See "
            "docs/reference/configuration-precedence.md for the wiring "
            "contract; either start the consuming service unconditionally "
            "or remove the setting. Per-setting opt-out: append "
            "'# lint-allow: bootstrap-wiring -- <reason>' on the "
            "register(...) closing line.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
