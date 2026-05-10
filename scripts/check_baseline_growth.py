"""Block commits that grow gate-suppression baselines.

Gate suppression baselines (``scripts/*_baseline.txt``,
``scripts/*_baseline.json``, ``scripts/_*_baseline.py``) record pre-existing
violations that the corresponding ``check_*`` gate is allowed to skip. The
baselines are intended to shrink monotonically -- a PR that makes the codebase
worse by silencing a new violation should fail the gate, not amend the
baseline.

This pre-commit hook diffs each baseline file against ``HEAD`` and rejects
the commit if the staged version contains *more* entries than the committed
version. Shrinking and rewording are allowed; growing is not.

Bypass (rare; requires explicit user approval): ``SKIP=baseline-growth git
commit ...`` or set ``ALLOW_BASELINE_GROWTH=1`` in the environment.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXIT_OK = 0
EXIT_GROWTH_DETECTED = 1
EXIT_INVALID_BASELINE = 2


class InvalidBaselineError(Exception):
    """Raised when a staged baseline file cannot be parsed."""


def _is_baseline_path(path: str) -> bool:
    """Return ``True`` for paths that this gate should compare against HEAD."""
    if not path.startswith("scripts/"):
        return False
    name = path[len("scripts/") :]
    if "/" in name:
        return False
    if name.endswith(("_baseline.txt", "_baseline.json")):
        return True
    return name.startswith("_") and name.endswith("_baseline.py")


def _count_json_entries(text: str) -> int:
    """Count entries in a JSON baseline; raise ``InvalidBaselineError`` on parse failure.

    A corrupt baseline must block the commit, never silently pass. The previous
    sentinel return of -1 was less-than every non-negative ``head_count``, which
    let malformed baselines slip through the ``staged > head`` comparison.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"baseline JSON failed to parse: {exc.msg} at line {exc.lineno}"
        raise InvalidBaselineError(msg) from exc
    if isinstance(payload, dict):
        locations = payload.get("locations")
        if isinstance(locations, dict):
            return len(locations)
        if isinstance(locations, list):
            return len(locations)
        return 0
    if isinstance(payload, list):
        return len(payload)
    return 0


def _count_text_entries(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _staged_entries(text: str, suffix: str) -> int:
    """Count the entries in a baseline file's content."""
    if suffix == ".json":
        return _count_json_entries(text)
    return _count_text_entries(text)


def _read_head(path: str) -> str | None:
    """Return the file at ``HEAD`` or ``None`` if it did not exist there.

    ``FileNotFoundError`` from a missing ``git`` binary is treated the same as
    "no HEAD content", matching the not-yet-committed-baseline path. Any other
    OS error is surfaced via stderr so a broken environment is visible rather
    than silently letting growth through.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"WARNING: git show failed for {path}: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _classify(path: str) -> str:
    if path.endswith(".json"):
        return ".json"
    if path.endswith(".py"):
        return ".py"
    return ".txt"


def _inspect_path(
    path: str,
    grown: list[tuple[str, int, int]],
    invalid: list[tuple[str, str]],
) -> None:
    """Compare one staged baseline against HEAD; record growth or parse failure."""
    suffix = _classify(path)
    absolute = REPO_ROOT / path
    try:
        staged_text = absolute.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        staged_count = _staged_entries(staged_text, suffix)
    except InvalidBaselineError as exc:
        invalid.append((path, str(exc)))
        return
    head_text = _read_head(path)
    head_count = 0
    if head_text is not None:
        try:
            head_count = _staged_entries(head_text, suffix)
        except InvalidBaselineError:
            head_count = 0
    if staged_count > head_count:
        grown.append((path, head_count, staged_count))


def main(argv: list[str]) -> int:
    """Compare staged baseline files against HEAD; reject growth."""
    if os.environ.get("ALLOW_BASELINE_GROWTH") == "1":
        return EXIT_OK
    paths = [p for p in argv[1:] if _is_baseline_path(p)]
    if not paths:
        return EXIT_OK
    grown: list[tuple[str, int, int]] = []
    invalid: list[tuple[str, str]] = []
    for path in paths:
        _inspect_path(path, grown, invalid)
    if invalid:
        print(
            "Gate suppression baselines failed to parse:",
            file=sys.stderr,
        )
        for path, reason in invalid:
            print(f"  {path}: {reason}", file=sys.stderr)
        return EXIT_INVALID_BASELINE
    if not grown:
        return EXIT_OK
    print(
        "Gate suppression baselines must shrink monotonically. "
        "These files have more entries than HEAD:",
        file=sys.stderr,
    )
    for path, head_count, staged_count in grown:
        print(
            f"  {path}: {head_count} -> {staged_count} (+{staged_count - head_count})",
            file=sys.stderr,
        )
    print(
        "\nFix the source instead, or ask the user before adding a new exception.\n"
        "Bypass (requires explicit user approval): "
        "ALLOW_BASELINE_GROWTH=1 git commit ...",
        file=sys.stderr,
    )
    return EXIT_GROWTH_DETECTED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
