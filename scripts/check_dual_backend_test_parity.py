"""Pre-push / CI gate: dual-backend test parity for persistence conformance.

Three passes over the conformance suite + the persistence package:

1. **Signature** -- every ``def test_*`` / ``async def test_*`` under
   ``tests/conformance/persistence/`` (excluding ``conftest.py``) must
   accept a ``backend`` parameter, and no parameter may be annotated
   with a concrete driver / backend type that bypasses the
   parametrisation seam (``aiosqlite.Connection``,
   ``psycopg.AsyncConnection``, ``psycopg.Connection``,
   ``psycopg_pool.AsyncConnectionPool``, ``SQLitePersistenceBackend``,
   ``PostgresPersistenceBackend``, ``SQLiteConfig``, ``PostgresConfig``).
2. **Body** -- a test function body that compares
   ``backend.backend_name`` against a string literal (``== "sqlite"``,
   ``!= "postgres"``, ...) silently turns the test into a one-arm
   conditional.  Issue #1751 names this as the second recurrence
   pattern; the gate flags it so deliberate exceptions stay visible.
3. **Coverage** -- every repository protocol class defined or
   re-exported under ``src/synthorg/persistence/*_protocol.py`` that is
   exposed on ``PersistenceBackend`` (via ``@property`` or method) must
   be exercised by at least one ``backend.<accessor>`` reference in the
   conformance suite.

The shared ``backend`` fixture in
``tests/conformance/persistence/conftest.py`` is parametrised over
``["sqlite", "postgres"]``, so any test consuming it automatically runs
against both backends.

Per-line opt-out: append ``# lint-allow: dual-backend-parity --
<reason>`` to any line of the test signature.  The justification after
``--`` is required and must be non-empty (mirrors
``# lint-allow: persistence-boundary``).  A marker on the signature
suppresses both the signature checks AND the body check for that test.

Baseline file ``scripts/dual_backend_parity_baseline.txt`` freezes
pre-existing violations.  New violations fail; stale baseline entries
warn (but pass) so the file ratchets down over time.  Regenerate via
``--update-baseline`` (commit the diff after manual review).

Helpers and dataclasses live in :mod:`_dual_backend_parity_lib`
(sibling module) so this entry-point stays under the 800-line ceiling.

Usage::

    python scripts/check_dual_backend_test_parity.py
    python scripts/check_dual_backend_test_parity.py --repo-root /path/to/repo
    python scripts/check_dual_backend_test_parity.py --baseline /path/to/file.txt
    python scripts/check_dual_backend_test_parity.py --update-baseline
"""

import argparse
import sys
from pathlib import Path

# Sibling-import dance, mirroring scripts/check_setting_to_startup_trace.py.
# Standalone CLI invocation needs the script's parent directory on
# sys.path; package-style invocation (e.g. tests loading via importlib)
# uses the ``scripts.`` prefix instead.
if __package__ in {None, ""}:  # standalone invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dual_backend_parity_lib import (  # type: ignore[import-not-found]
        _apply_baseline,
        _build_coverage_violations,
        _collect_backend_accessor_usage,
        _collect_body_violations,
        _collect_coverage_violations,
        _collect_signature_violations,
        _CoverageViolation,
        _discover_backend_accessors,
        _discover_repo_classes,
        _load_baseline,
        _scan_signature_file,
        _TestViolation,
        _write_baseline,
    )
else:
    from scripts._dual_backend_parity_lib import (  # type: ignore[import-not-found]
        _apply_baseline,
        _build_coverage_violations,
        _collect_backend_accessor_usage,
        _collect_body_violations,
        _collect_coverage_violations,
        _collect_signature_violations,
        _CoverageViolation,
        _discover_backend_accessors,
        _discover_repo_classes,
        _load_baseline,
        _scan_signature_file,
        _TestViolation,
        _write_baseline,
    )

# Re-exports for the test importlib-loader pattern.
__all__ = (
    "ProjectRootError",
    "_CoverageViolation",
    "_TestViolation",
    "_apply_baseline",
    "_collect_backend_accessor_usage",
    "_collect_body_violations",
    "_collect_coverage_violations",
    "_collect_signature_violations",
    "_discover_backend_accessors",
    "_discover_repo_classes",
    "_load_baseline",
    "_scan_signature_file",
    "_write_baseline",
    "main",
)


# ── Top-level scan ──────────────────────────────────────────────


def _scan_test_pass(
    conformance_dir: Path,
    project_root: Path,
) -> list[tuple[str, str]]:
    """Return ``(baseline_key, message)`` for every test-pass violation.

    Walks every ``.py`` under *conformance_dir* except ``conftest.py``
    (legitimately constructs concrete backends inside the parametrised
    fixture) and ``__init__.py`` (no test functions).
    """
    if not conformance_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(conformance_dir.rglob("*.py")):
        if path.name in {"conftest.py", "__init__.py"}:
            continue
        try:
            rel_path = path.relative_to(project_root).as_posix()
        except ValueError:
            rel_path = path.name
        out.extend(
            (v.baseline_key(), v.message())
            for v in _collect_signature_violations(path, rel_path)
        )
    return out


def _scan_coverage_pass(
    persistence_dir: Path,
    conformance_dir: Path,
) -> list[tuple[str, str]]:
    """Return ``(baseline_key, message)`` for every coverage violation."""
    repo_classes = _discover_repo_classes(persistence_dir)
    accessor_for = _discover_backend_accessors(persistence_dir / "protocol.py")
    used_accessors = _collect_backend_accessor_usage(conformance_dir)
    return [
        (v.baseline_key(), v.message())
        for v in _build_coverage_violations(repo_classes, accessor_for, used_accessors)
    ]


def _scan_repo(project_root: Path) -> list[tuple[str, str]]:
    """Run all passes; return ``[(baseline_key, message), ...]`` sorted."""
    persistence_dir = project_root / "src" / "synthorg" / "persistence"
    conformance_dir = project_root / "tests" / "conformance" / "persistence"
    out: list[tuple[str, str]] = []
    out.extend(_scan_test_pass(conformance_dir, project_root))
    out.extend(_scan_coverage_pass(persistence_dir, conformance_dir))
    out.sort(key=lambda pair: pair[0])
    return out


# ── CLI ─────────────────────────────────────────────────────────


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments."""
    default_root = Path(__file__).resolve().parent.parent
    if repo_root is None:
        return default_root
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911 -- distinct exit codes
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to scan. Defaults to the script's repo. "
            "Pass ${{ github.workspace }} in CI to remove ambiguity."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file. Defaults to "
            "scripts/dual_backend_parity_baseline.txt under the resolved "
            "repo root."
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
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    baseline_path = args.baseline or (
        project_root / "scripts" / "dual_backend_parity_baseline.txt"
    )

    try:
        pairs = _scan_repo(project_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    current_keys = {key for key, _ in pairs}
    message_for = dict(pairs)

    if args.update_baseline:
        try:
            _write_baseline(baseline_path, current_keys)
        except OSError as exc:
            print(
                f"Cannot write baseline {baseline_path.as_posix()}: {exc}",
                file=sys.stderr,
            )
            return 2
        print(
            f"Wrote {len(current_keys)} entries to {baseline_path.as_posix()}.",
            file=sys.stderr,
        )
        return 0

    try:
        baseline = _load_baseline(baseline_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    new, stale = _apply_baseline(current_keys, baseline)

    for key in new:
        print(message_for[key], file=sys.stderr)

    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python scripts/check_dual_backend_test_parity.py "
            "--update-baseline' once the fix has merged.",
            file=sys.stderr,
        )

    if new:
        print(
            f"\n{len(new)} new dual-backend parity violation(s). Either fix the "
            "test signature (use `backend: PersistenceBackend`), remove the "
            "`backend.backend_name == ...` conditional, add a Test class that "
            "exercises the missing repo, or add the per-line opt-out "
            "'# lint-allow: dual-backend-parity -- <reason>' on the test "
            "signature when the deviation is genuinely sanctioned.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
