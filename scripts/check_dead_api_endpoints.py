#!/usr/bin/env python3
"""Pre-push / CI gate: dead API endpoint detector.

Catches frontend (``web/src/``) call sites whose URL is not
registered as a backend Litestar route. Backend routes with no
frontend caller are reported informationally but never block the
gate (they may legitimately be CLI-only or public-REST surfaces).

Why this exists
---------------

Naive regex extraction of frontend API calls misses conditionally-
registered controllers, websocket handlers, Router-prefixed paths,
and Litestar ``{var:type}`` vs frontend ``${var}`` path-param syntax.
This gate handles those shapes natively via AST + token scanning so
mismatches are caught at pre-commit time rather than in retrospective
audits.

Per-line opt-out: append ``// lint-allow: dead-api-endpoints --
<reason>`` (TS) or ``# lint-allow: dead-api-endpoints -- <reason>``
(Python) on the call-site line. The justification after ``--`` is
required and must be non-empty (mirrors
``# lint-allow: persistence-boundary``).

Baseline file: ``scripts/dead_api_endpoints_baseline.txt`` freezes
pre-existing high-severity findings so the lint can ship without
forcing the full repair in the same PR. Pass-condition: current
violations ⊆ baseline; fail when new violations appear; warn (but
pass) when baseline entries are stale. Regenerate via
``--update-baseline`` (explicit user approval required).

Implementation is split across sibling private modules to stay
under the 800-line per-file ceiling (CLAUDE.md):

- ``_dead_api_endpoints_models``   -- dataclasses + suppression markers.
- ``_dead_api_endpoints_backend``  -- AST walker for Litestar routes.
- ``_dead_api_endpoints_frontend`` -- TS scanner for call sites.
- ``_dead_api_endpoints_compare``  -- diff + baseline I/O.

Usage::

    uv run python scripts/check_dead_api_endpoints.py
    uv run python scripts/check_dead_api_endpoints.py --update-baseline
    uv run python scripts/check_dead_api_endpoints.py --repo-root /path/to/repo
"""

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dead_api_endpoints_backend import (  # type: ignore[import-not-found]
        collect_backend_routes,
    )
    from _dead_api_endpoints_compare import (  # type: ignore[import-not-found]
        compare,
        filter_against_baseline,
        load_baseline,
        write_baseline,
    )
    from _dead_api_endpoints_frontend import (  # type: ignore[import-not-found]
        collect_frontend_call_sites,
    )
    from _dead_api_endpoints_models import (  # type: ignore[import-not-found]
        CallSiteRecord,
        RouteRecord,
        Violation,
    )
else:
    from scripts._dead_api_endpoints_backend import collect_backend_routes
    from scripts._dead_api_endpoints_compare import (
        compare,
        filter_against_baseline,
        load_baseline,
        write_baseline,
    )
    from scripts._dead_api_endpoints_frontend import collect_frontend_call_sites
    from scripts._dead_api_endpoints_models import (
        CallSiteRecord,
        RouteRecord,
        Violation,
    )

__all__ = (
    "CallSiteRecord",
    "RouteRecord",
    "Violation",
    "collect_backend_routes",
    "collect_frontend_call_sites",
    "compare",
    "filter_against_baseline",
    "load_baseline",
    "main",
    "write_baseline",
)


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root, defaulting to this script's repo."""
    if repo_root is not None:
        return repo_root.resolve(strict=True)
    return Path(__file__).resolve().parent.parent


def _format_violation(v: Violation) -> str:
    """One-line stdout violation report."""
    return (
        f"{v.source_file}:{v.source_line}:{v.source_col}: "
        f"[{v.severity}] {v.method} {v.path} -- {v.reason}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to scan. Defaults to the script's repo (parent of scripts/)."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file. Defaults to "
            "scripts/dead_api_endpoints_baseline.txt under the resolved "
            "repo root."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Overwrite the baseline file with the current high-severity "
            "violation set. Commit the diff after manual review."
        ),
    )
    parser.add_argument(
        "--api-prefix",
        default="/api/v1",
        help=(
            "Backend API prefix to strip when comparing against frontend "
            "URLs (default ``/api/v1``)."
        ),
    )
    parser.add_argument(
        "--show-info",
        action="store_true",
        help="Print orphan-backend (info-severity) findings as well.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except OSError as exc:
        print(f"--repo-root not accessible: {exc}", file=sys.stderr)
        return 2

    baseline_path = args.baseline or (
        project_root / "scripts" / "dead_api_endpoints_baseline.txt"
    )

    try:
        backend_routes = collect_backend_routes(
            project_root, api_prefix=args.api_prefix
        )
    except ValueError as exc:
        print(f"check_dead_api_endpoints: {exc}", file=sys.stderr)
        return 2
    frontend_calls = collect_frontend_call_sites(project_root)
    high, info = compare(backend_routes, frontend_calls)

    if args.update_baseline:
        try:
            write_baseline(high, baseline_path)
        except OSError as exc:
            print(
                f"Cannot write baseline {baseline_path.as_posix()}: {exc}",
                file=sys.stderr,
            )
            return 2
        print(
            f"Wrote {len(high)} entries to {baseline_path.as_posix()}.",
            file=sys.stderr,
        )
        return 0

    try:
        unbaselined, stale = filter_against_baseline(high, baseline_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for v in unbaselined:
        print(_format_violation(v))

    if args.show_info:
        for v in info:
            print(_format_violation(v))

    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python scripts/check_dead_api_endpoints.py "
            "--update-baseline' once the fix has merged.",
            file=sys.stderr,
        )

    if unbaselined:
        print(
            f"\n{len(unbaselined)} new dead-API-endpoint finding(s). Either "
            "wire the backend route, fix the frontend caller, or add "
            "'// lint-allow: dead-api-endpoints -- <reason>' on the "
            "call-site line.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
