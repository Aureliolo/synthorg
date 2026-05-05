"""Comparator + baseline I/O for the dead-API gate.

The comparator runs after the backend AST walker
(:mod:`scripts._dead_api_endpoints_backend`) and the frontend scanner
(:mod:`scripts._dead_api_endpoints_frontend`) have produced their
inventories. It diffs the two and emits two severity buckets:

- ``high`` -- a frontend call site whose path is not registered by
  the backend. This is the case the gate exists to catch and the
  only one that fails the run (subject to baseline subtraction).
- ``info`` -- a backend route with no frontend caller. Printed but
  never blocking; the codebase legitimately exposes some routes for
  CLI / public REST clients.

Path comparison is exact-string after both sides have flowed through
:func:`scripts._dead_api_endpoints_models.normalise_path` -- every
path-param ``{anything}`` collapses to ``{*}``, so positional matches
against ``{name:str}`` / ``{agent_id:str}`` / ``${var}`` succeed
regardless of name. Methods are uppercased.
"""

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dead_api_endpoints_models import (  # type: ignore[import-not-found]
        _BASELINE_FIELDS,
        _BASELINE_HEADER,
        CallSiteRecord,
        RouteRecord,
        Violation,
    )
else:
    from scripts._dead_api_endpoints_models import (
        _BASELINE_FIELDS,
        _BASELINE_HEADER,
        CallSiteRecord,
        RouteRecord,
        Violation,
    )


def compare(
    backend_routes: list[RouteRecord],
    frontend_calls: list[CallSiteRecord],
) -> tuple[list[Violation], list[Violation]]:
    """Return ``(high_violations, info_violations)``.

    ``high_violations`` -- frontend → backend mismatches (block the gate).
    ``info_violations`` -- backend orphans (do not block).

    Suppressed call sites (``has_suppression=True``) are excluded
    from the high list outright; backend orphans are not subject to
    the suppression marker because there is no source line to attach
    one to.
    """
    backend_keys: set[tuple[str, str]] = {(r.method, r.path) for r in backend_routes}
    frontend_keys: set[tuple[str, str]] = {(c.method, c.path) for c in frontend_calls}

    high: list[Violation] = []
    for call in frontend_calls:
        if call.has_suppression:
            continue
        if (call.method, call.path) in backend_keys:
            continue
        high.append(
            Violation(
                severity="high",
                method=call.method,
                path=call.path,
                source_file=call.source_file,
                source_line=call.source_line,
                source_col=call.source_col,
                reason=(
                    f"frontend calls {call.method} {call.path} but no "
                    "backend route is registered for this method+path. "
                    "Either register the controller route or update the "
                    "frontend caller. Per-line opt-out: "
                    "// lint-allow: dead-api-endpoints -- <reason>."
                ),
            )
        )

    info: list[Violation] = []
    for route in backend_routes:
        if (route.method, route.path) in frontend_keys:
            continue
        info.append(
            Violation(
                severity="info",
                method=route.method,
                path=route.path,
                source_file=route.source_file,
                source_line=route.source_line,
                source_col=0,
                reason=(
                    f"backend route {route.method} {route.path} "
                    f"({route.controller_name}) has no frontend caller. "
                    "Informational only: keep if the route is consumed by "
                    "CLI / public REST clients."
                ),
            )
        )

    high.sort(key=lambda v: v.baseline_key())
    info.sort(key=lambda v: (v.method, v.path))
    return high, info


# ── Baseline I/O ───────────────────────────────────────────────


def load_baseline(path: Path) -> set[str]:
    """Parse a baseline file into a set of ``file:line:col:method:path`` keys.

    Blank lines and ``#`` comment lines are ignored. Other lines must
    match the expected five-field shape; malformed entries raise so
    the gate fails loud rather than silently dropping suppressions.

    Raises:
        ValueError: When the baseline file exists but cannot be read
            or contains a malformed entry.
    """
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Cannot read baseline file {path.as_posix()}: {exc}"
        raise ValueError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"Baseline file {path.as_posix()} has encoding error: {exc}"
        raise ValueError(msg) from exc
    entries: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # The path field can contain ``:`` (it does not in current
        # data, but be defensive). Split into the first 4 fields plus
        # the remainder so the URL captures any colons after it.
        parts = stripped.split(":", _BASELINE_FIELDS - 1)
        if len(parts) != _BASELINE_FIELDS or not all(p for p in parts):
            msg = (
                f"{path.as_posix()}:{lineno}: malformed baseline entry "
                f"(expected '<file>:<line>:<col>:<method>:<path>', "
                f"got {stripped!r})"
            )
            raise ValueError(msg)
        entries.add(stripped)
    return entries


def write_baseline(violations: list[Violation], path: Path) -> None:
    """Overwrite the baseline file with sorted current-violation keys."""
    body = _BASELINE_HEADER + "\n".join(v.baseline_key() for v in violations) + "\n"
    path.write_text(body, encoding="utf-8")


def filter_against_baseline(
    new_violations: list[Violation],
    baseline_path: Path,
) -> tuple[list[Violation], list[str]]:
    """Subtract baseline keys from *new_violations*; report stale entries.

    Returns ``(unbaselined, stale_baseline_keys)``:

    - ``unbaselined`` -- violations whose baseline key is NOT in the
      baseline file (these block the gate).
    - ``stale_baseline_keys`` -- baseline entries that no longer
      correspond to a current violation (warning-only; the gate
      still passes).
    """
    baseline_keys = load_baseline(baseline_path)
    current_keys = {v.baseline_key() for v in new_violations}
    unbaselined = [v for v in new_violations if v.baseline_key() not in baseline_keys]
    stale = sorted(baseline_keys - current_keys)
    return unbaselined, stale
