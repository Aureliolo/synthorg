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
import re
import subprocess
import sys

EXIT_OK = 0
EXIT_GROWTH_DETECTED = 1
EXIT_INVALID_BASELINE = 2

_SCRIPTS_DIRNAME = "scripts"

# Single regex governing every "is this a gate-suppression baseline" decision.
# Three legitimate shapes:
#   - ``<name>_baseline.txt`` / ``<name>_baseline.json`` (optional leading ``_``)
#   - ``_<name>_baseline.py`` (leading ``_`` REQUIRED for py-format baselines)
# Everything else is rejected so ``_is_baseline_path`` and any downstream check
# stay consistent: a previous split between an ``endswith()`` filter and a
# regex sanitiser let some shapes pass one check and not the other.
_BASELINE_BASENAME_RE = re.compile(
    r"^(?:_?[a-z][a-z_]*_baseline\.(?:txt|json)|_[a-z][a-z_]*_baseline\.py)$"
)


class InvalidBaselineError(Exception):
    """Raised when a staged baseline file cannot be parsed."""


def _is_baseline_path(path: str) -> bool:
    """Return ``True`` for paths this gate should compare against HEAD.

    Validates against ``_BASELINE_BASENAME_RE`` so every code path agrees on
    which shapes count as a baseline. Rejects nested paths and any basename
    containing path separators (POSIX or Windows-style).
    """
    if not path.startswith(f"{_SCRIPTS_DIRNAME}/"):
        return False
    basename = path[len(_SCRIPTS_DIRNAME) + 1 :]
    if "/" in basename or "\\" in basename:
        return False
    return _BASELINE_BASENAME_RE.fullmatch(basename) is not None


def _count_json_entries(text: str) -> int:
    """Count entries in a JSON baseline; raise ``InvalidBaselineError`` on parse failure.

    A corrupt baseline must block the commit, never silently pass. The previous
    sentinel return of -1 was less-than every non-negative ``head_count``, which
    let malformed baselines slip through the ``staged > head`` comparison.

    For dict payloads, prefer the ``locations`` key (the canonical shape used
    by gate baselines). When ``locations`` is missing or non-collection, fall
    back to counting top-level keys so a flat-dict baseline format still
    surfaces growth instead of silently returning 0.
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
        return len(payload)
    if isinstance(payload, list):
        return len(payload)
    return 0


def _count_text_entries(text: str) -> int:
    """Count non-blank, non-comment lines in a text baseline."""
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
    """Return the file-suffix tag (``.json`` / ``.py`` / ``.txt``) for a baseline path."""
    if path.endswith(".json"):
        return ".json"
    if path.endswith(".py"):
        return ".py"
    return ".txt"


def _read_staged(path: str) -> str | None:
    """Return the staged-blob content for ``path`` from the git index, or ``None``.

    Reads via ``git show :<path>`` so the gate compares what is actually
    staged for the commit, not whatever happens to be on disk. A working-tree
    read could drift from the index (post-stage edit, file removed after
    staging) and let growth slip through. Reading from the index also avoids
    any filesystem touch on the user-controlled argv path, closing the
    path-injection class entirely.

    Treats a missing ``git`` binary, the path not being staged, and other OS
    errors as "no staged content" so the caller skips silently rather than
    crashing on infrastructure problems pre-commit cannot reach in practice.
    """
    try:
        result = subprocess.run(
            ["git", "show", f":{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"WARNING: git show :{path} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _inspect_path(
    path: str,
    grown: list[tuple[str, int, int]],
    invalid: list[tuple[str, str]],
) -> None:
    """Compare one staged baseline against HEAD; record growth or parse failure."""
    suffix = _classify(path)
    staged_text = _read_staged(path)
    if staged_text is None:
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
        except InvalidBaselineError as exc:
            print(
                f"WARNING: HEAD baseline {path} failed to parse ({exc}); "
                "falling back to head_count=0. The growth check may be "
                "over-strict for this file until HEAD is repaired.",
                file=sys.stderr,
            )
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
