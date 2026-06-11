"""Regression gate: no inline ``# mypy:`` comment may lift explicit-Any / unused-ignore.

The global ``[tool.mypy] disallow_any_explicit = true`` (plus the now-enabled
``unused-ignore`` error) holds across all of ``src/`` and ``tests/``; the
irreducible explicit-``Any`` sites carry reasoned per-line
``# type: ignore[explicit-any]`` suppressions.

``check_no_synthorg_any_override.py`` guards the ``pyproject.toml``
``[[tool.mypy.overrides]]`` vector. This gate guards the second vector: a
module-level ``# mypy:`` configuration comment inside a source file, which mypy
honours per-module and which the override gate cannot see. mypy lifts the two
flags via a file-level comment in several equivalent forms, all blocked here:

* ``# mypy: disable-error-code="explicit-any"`` (or ``unused-ignore``), bare or
  quoted, alone or among other comma- or space-separated codes;
* ``# mypy: disallow-any-explicit = False`` (dash or underscore spelling; any
  configparser-falsy value -- ``false / no / off / 0``, case-insensitive);
* ``# mypy: warn-unused-ignores = False`` (which stops ``unused-ignore`` erroring);
* ``# mypy: ignore-errors`` / ``ignore-errors = True`` (any truthy value --
  ``true / yes / on / 1``; silences every error, explicit-``Any`` and
  ``unused-ignore`` included).

A per-line ``# type: ignore[explicit-any]`` is the sanctioned escape hatch and is
NOT a ``# mypy:`` directive, so it is never flagged. File-level disables of other
codes (``union-attr``, ``arg-type``, ``empty-body``, ...) are legitimate and out
of scope.

Usage:
    uv run python scripts/check_no_explicit_any_inline_disable.py
    uv run python scripts/check_no_explicit_any_inline_disable.py path/to/file.py
    uv run python scripts/check_no_explicit_any_inline_disable.py --repo-root .

Exit codes:
    0 -- no inline ``# mypy:`` comment lifts the flags.
    1 -- a forbidden inline directive was found.
    2 -- configuration error (an invalid ``--repo-root``).
"""

import argparse
import io
import re
import sys
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

# Directories whose modules inherit the global mypy config (the surface
# ``mypy src/ tests/`` type-checks); an inline lift here defeats the flag.
_SCAN_DIRS: Final[tuple[str, ...]] = ("src", "tests")

# A module-level mypy configuration comment: ``# mypy: <directives>``. The
# ``type: ignore`` per-line form is a distinct ``# type:`` comment and never
# matches this, so the sanctioned per-line escape hatch is left untouched.
_MYPY_DIRECTIVE: Final[re.Pattern[str]] = re.compile(r"#\s*mypy:\s*(?P<body>.+)$")

# mypy parses an inline boolean via configparser, which reads
# ``0 / no / off / false`` (case-insensitive) as False and ``1 / yes / on /
# true`` as True. A flag-lift must match every falsy spelling, not just ``False``.
_FALSY: Final[str] = r"(?i:false|no|off|0)"

# The disabled-codes value: a quoted list (commas live inside the quotes) or a
# bare value running to the next setting (comma) or end of line. mypy accepts
# comma- AND space-separated codes, so the bare branch captures the whole value
# and ``_disabled_codes`` splits on either separator.
_DISABLE_CODE: Final[re.Pattern[str]] = re.compile(
    r"disable[-_]error[-_]code\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,\n]+)"
)

# The two booleans whose flip re-opens explicit-Any (``disallow-any-explicit =
# False``) or unused-ignore (``warn-unused-ignores = False``); any falsy spelling
# lifts the flag.
_FALSE_FLAG: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<flag>disallow[-_]any[-_]explicit|warn[-_]unused[-_]ignores)\b"
    rf"\s*=\s*{_FALSY}\b"
)

# ``ignore-errors`` silences every error unless explicitly set to a falsy value;
# the flag-only form and every truthy spelling (``= True / yes / on / 1``) lift
# the checks.
_IGNORE_ERRORS: Final[re.Pattern[str]] = re.compile(
    rf"\bignore[-_]errors\b(?!\s*=\s*{_FALSY}\b)"
)

_LIFTED_CODES: Final[frozenset[str]] = frozenset({"explicit-any", "unused-ignore"})


def _disabled_codes(value: str) -> list[str]:
    """Split a ``disable-error-code`` value into its individual error codes.

    mypy accepts both comma- and space-separated codes (``"a, b"`` and
    ``a b``), so split on either separator.
    """
    stripped = value.strip().strip("\"'")
    return [code for code in re.split(r"[,\s]+", stripped) if code]


def _classify(body: str) -> str | None:
    """Return a violation reason for a ``# mypy:`` directive body, or None."""
    for code_match in _DISABLE_CODE.finditer(body):
        lifted = sorted(set(_disabled_codes(code_match.group("value"))) & _LIFTED_CODES)
        if lifted:
            return f"disable-error-code lifts {', '.join(lifted)}"

    flag_match = _FALSE_FLAG.search(body)
    if flag_match is not None:
        flag = flag_match.group("flag").replace("_", "-")
        return f"{flag} set to a falsy value re-opens the flag"

    if _IGNORE_ERRORS.search(body) is not None:
        return "ignore-errors silences explicit-any"
    return None


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, reason)`` for each lifting ``# mypy:`` directive.

    Only genuine comment tokens are inspected (via ``tokenize``), so a ``# mypy:``
    directive quoted inside a string literal or docstring -- as this gate's own
    test fixtures do -- is never mistaken for a real module-level configuration
    comment. Pure over a file's content so the gate is unit-testable without
    touching the filesystem; line numbers are 1-based. A file that does not
    tokenise (a syntax error mypy would itself reject) yields no violations.
    """
    violations: list[tuple[int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError, SyntaxError:
        # ``IndentationError`` is a ``SyntaxError`` subclass; a file mypy would
        # itself reject yields no violations here.
        return violations
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        match = _MYPY_DIRECTIVE.search(token.string)
        if match is None:
            continue
        reason = _classify(match.group("body"))
        if reason is not None:
            violations.append((token.start[0], reason))
    return violations


def _candidate_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under the scanned directories, in sorted order."""
    files: list[Path] = []
    for rel in _SCAN_DIRS:
        base = root / rel
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def find_violations(paths: Iterable[Path]) -> list[str]:
    """Return human-readable violation strings for every offending file/line.

    Raises:
        OSError: If a target file cannot be read. An unreadable target is a
            configuration error (exit 2), distinct from a directive violation
            (exit 1); the caller routes it accordingly.
    """
    messages: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for lineno, reason in scan_text(text):
            messages.append(f"{path}:{lineno}: forbidden inline # mypy: -- {reason}")
    return messages


def main(argv: Sequence[str] | None = None) -> int:
    """Scan inline ``# mypy:`` directives and return the gate exit code.

    Explicit file paths (as pre-commit passes) are scanned directly; with none,
    the gate walks ``src/`` and ``tests/`` under ``--repo-root``. Exit codes are
    0 (clean), 1 (a forbidden directive), and 2 (config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("paths", nargs="*", help="Specific files to scan.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    if args.paths:
        targets = [Path(p) for p in args.paths if p.endswith(".py")]
    else:
        targets = _candidate_files(root)

    try:
        violations = find_violations(targets)
    except OSError as exc:
        print(f"error: could not read a target file: {exc}", file=sys.stderr)
        return 2
    if not violations:
        return 0

    for message in violations:
        print(message, file=sys.stderr)
    print(
        "A module-level # mypy: comment must not lift disallow_any_explicit or "
        "unused-ignore. Drain the explicit Any (object for arbitrary params, a "
        "concrete type, or tests._shared.JsonDict for navigable JSON), or "
        "suppress an irreducible site with a reasoned per-line "
        "# type: ignore[explicit-any]. Remove stale ignores rather than "
        "disabling unused-ignore.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
