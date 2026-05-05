"""Sibling module of ``scripts/check_schema_drift.py``: baseline I/O.

Reads + writes ``scripts/schema_drift_baseline.txt``: the frozen
list of currently-tolerated drift entries.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

if __package__ in {None, ""}:
    from _schema_drift_models import (  # type: ignore[import-not-found]
        BASELINE_FIELD_COUNTS,
    )
else:
    from ._schema_drift_models import BASELINE_FIELD_COUNTS


def load_baseline(path: Path) -> set[str]:
    """Load the baseline file and return its set of canonical keys.

    Comments (``#``-prefixed) and blank lines are skipped silently.
    Every other line is split on ``:`` per ``BASELINE_FIELD_COUNTS``.

    Raises:
        ValueError: If a line carries an unknown kind, has too few
            fields for its kind, or has a whitespace-only reason.
            The error message includes the line number so operators
            can locate the bad entry directly.
    """
    return set(load_baseline_with_reasons(path).keys())


def load_baseline_with_reasons(path: Path) -> dict[str, str]:
    """Load the baseline file as a ``{canonical_key: reason}`` mapping.

    Same validation as :func:`load_baseline`; returns the per-entry
    reason text alongside each key so callers can preserve those
    reasons across baseline regeneration (e.g. ``--update-baseline``).
    """
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    with path.open(encoding="utf-8") as fp:
        for line_num, raw_line in enumerate(fp, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, reason = _parse_baseline_line(line, line_num)
            entries[key] = reason
    return entries


def _parse_baseline_line(line: str, line_num: int) -> tuple[str, str]:
    """Validate one baseline line; return ``(canonical_key, reason)``."""
    kind = line.split(":", 1)[0]
    if kind not in BASELINE_FIELD_COUNTS:
        msg = f"baseline line {line_num}: unknown kind {kind!r}: {line!r}"
        raise ValueError(msg)
    expected = BASELINE_FIELD_COUNTS[kind]
    fields = line.split(":", expected - 1)
    if len(fields) < expected:
        msg = (
            f"baseline line {line_num}: too few fields "
            f"({len(fields)} < {expected}): {line!r}"
        )
        raise ValueError(msg)
    reason = fields[-1].strip()
    if not reason:
        msg = f"baseline line {line_num}: empty reason field: {line!r}"
        raise ValueError(msg)
    return ":".join(fields[:-1]), reason


_BASELINE_HEADER: str = (
    "# Frozen baseline of intentional SQLite <-> Postgres schema drift.\n"
    "# Each non-comment line is `<kind>:<key fields>:<reason>` where the\n"
    "# trailing reason field is required and must be non-empty.\n"
    "#\n"
    "# scripts/check_schema_drift.py reads this file to suppress\n"
    "# findings at these exact entries. New findings NOT in this list\n"
    "# fail the pre-push hook.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) with:\n"
    "#   uv run python scripts/check_schema_drift.py --update-baseline\n"
    "#\n"
    "# Per #1750 / audit cluster #8.\n"
)


def write_baseline(
    path: Path,
    findings: list[str],
    *,
    default_reason: str,
    reasons: Mapping[str, str] | None = None,
) -> None:
    """Write a fresh baseline file from *findings*.

    For each finding key:

    - If *reasons* contains the key, use the per-entry reason verbatim.
      Callers should pass the result of :func:`load_baseline_with_reasons`
      from the existing baseline so previously-edited justifications
      survive regeneration.
    - Otherwise, use *default_reason* as a placeholder; the operator
      hand-edits before commit so each entry documents its specific
      business or technical justification.

    The header references the PR / audit cluster that motivated the
    gate so future readers can reconstruct the why.

    Raises:
        OSError: If the parent directory does not exist or is not
            writable. The CLI catches and translates into exit code 2.
    """
    reason_map: Mapping[str, str] = reasons if reasons is not None else {}
    body = "\n".join(
        f"{key}:{reason_map.get(key, default_reason)}" for key in sorted(findings)
    )
    path.write_text(f"{_BASELINE_HEADER}{body}\n", encoding="utf-8")
