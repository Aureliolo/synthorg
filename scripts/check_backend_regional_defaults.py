"""Backend regional-defaults lint.

Scans ``src/synthorg/`` Python files for hardcoded ISO 4217 currency codes,
BCP 47 locale literals, ``_usd`` identifier suffixes, and application-code
``localhost:<port>`` references.  Mirrors ``scripts/check_web_design_system.py``
for the Python surface.

Runs as a PostToolUse hook on Edit/Write to ``src/synthorg/**/*.py``.  Also
supports direct CLI invocation for testing.

Exit codes:
    0 -- no issues found, file not in ``src/synthorg/``, or file could not be
         read (warnings printed to stderr for read/parse failures)
    1 -- violations found (prints warnings to stdout)

Opt-out: ``# lint-allow: regional-defaults`` on the offending line (or the
immediately preceding line) suppresses the check for that line.  Use
sparingly and only for code that legitimately demonstrates a specific
currency / locale (e.g. the ``budget.currency`` symbol table itself).

Usage (hook mode -- reads JSON from stdin):
    echo '{"tool_input":{"file_path":"src/synthorg/engine/foo.py"}}' |
        python scripts/check_backend_regional_defaults.py

Usage (CLI mode -- for testing / whole-tree sweep):
    python scripts/check_backend_regional_defaults.py <file_path>
    python scripts/check_backend_regional_defaults.py --all
"""

import argparse
import io
import json
import re
import sys
import tokenize
from collections.abc import Iterable
from pathlib import Path
from typing import Final

_SUPPRESSION_MARKER: Final[str] = "lint-allow: regional-defaults"

# Files where a curated mapping of ISO 4217 codes / locales is the actual
# payload of the module; linting these would be like linting a dictionary
# for containing its own keys.
_CURRENCY_ALLOWLIST_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/budget/currency.py",
        "src/synthorg/budget/config.py",
        "src/synthorg/core/types.py",
        "src/synthorg/settings/definitions/budget.py",
        "src/synthorg/settings/definitions/display.py",
    }
)
_LOCALE_ALLOWLIST_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/settings/definitions/display.py",
    }
)
# Intentionally correct-by-design localhost references (docker DNS,
# operator-host CLI output, NATS monitoring, local-provider presets).
_LOCALHOST_ALLOWLIST_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/api/config.py",
        "src/synthorg/communication/config.py",
        "src/synthorg/persistence/config.py",
        "src/synthorg/providers/presets.py",
        "src/synthorg/providers/discovery.py",
        "src/synthorg/settings/definitions/api.py",
        "src/synthorg/workers/__main__.py",
        "src/synthorg/memory/embedding/fine_tune_runner.py",
    }
)

# Currency codes curated in ``synthorg.budget.currency``; anything outside
# that set in application code is a typo or leaked hardcode.
_ISO_4217_CODES: Final[frozenset[str]] = frozenset(
    {
        "AUD",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "CZK",
        "DKK",
        "EUR",
        "GBP",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "JPY",
        "KRW",
        "MXN",
        "NOK",
        "NZD",
        "PLN",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "TWD",
        "USD",
        "VND",
        "ZAR",
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "ISK",
        "KMF",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
        "BHD",
        "IQD",
        "JOD",
        "KWD",
        "LYD",
        "OMR",
        "TND",
    }
)

# Match quoted 3-uppercase-letter strings that look like ISO 4217 codes.
_HARDCODED_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?<![A-Z_])                 # left boundary: no identifier char
        ['"]([A-Z]{3})['"]          # quoted 3-uppercase-letter code
        (?![A-Z_])                  # right boundary""",
    re.VERBOSE,
)

# Match currency-symbol-adjacent-to-digit in string literals.
_HARDCODED_CURRENCY_SYMBOL_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"][^'"]*                  # start of a string literal
        (?:\$|\u20ac|\u00a3|\u00a5) # common currency symbols
        \d                          # immediately followed by digit
        [^'"]*['"]""",
    re.VERBOSE,
)

# Identifiers ending in ``_usd`` (field names, variable names).
_USD_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"""\b                          # word boundary
        [A-Za-z_][A-Za-z0-9_]*_usd  # ident ending in _usd
        \b""",
    re.VERBOSE,
)

# BCP 47 locale literals (language-region).
_HARDCODED_LOCALE_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"]                        # opening quote
        ([a-z]{2,3}                 # language subtag
        -[A-Z]{2,3})                # region subtag
        ['"]""",
    re.VERBOSE,
)

# localhost:<port> in application code (outside allowlisted infra defaults).
_LOCALHOST_PORT_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?:localhost|127\.0\.0\.1)  # host
        :\d+                        # port""",
    re.VERBOSE,
)


def _line_has_dedicated_marker(line: str) -> bool:
    """Return True iff *line* is a whole-line marker comment.

    Matches exactly ``#`` + optional whitespace + the marker string (no
    other text).  Rejects inline-after-code markers and markers embedded
    in longer comments (e.g. ``# TODO lint-allow: regional-defaults
    later``) so neither form bleeds into the following line.
    """
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    return stripped[1:].strip() == _SUPPRESSION_MARKER


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    Uses Python's :mod:`tokenize` so ``#`` characters inside string
    literals (e.g. ``x = "# lint-allow: regional-defaults"``) do not
    masquerade as suppression comments.  The marker must either
    exactly match ``_SUPPRESSION_MARKER`` or be followed by whitespace
    so substring prefixes like
    ``lint-allow: regional-defaults-in-a-word`` are rejected.

    If :mod:`tokenize` fails on the line (rare -- usually an
    unterminated triple-quoted string that continues on the next line),
    we conservatively return ``False`` so the gate fails closed rather
    than suppressing a real violation.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if comment == _SUPPRESSION_MARKER:
            return True
        if comment.startswith(_SUPPRESSION_MARKER + " "):
            return True
    return False


def _is_suppressed(lines: list[str], idx: int) -> bool:
    """Return ``True`` when a lint-allow marker applies to line ``idx``.

    The marker suppresses either (a) the same line when it appears as a
    trailing ``#`` comment (not inside a string literal), or (b) the
    line immediately below a dedicated ``# lint-allow: regional-defaults``
    comment line.  Anything else -- markers inside string literals,
    inline markers on the *previous* line, or markers embedded in
    longer comments -- does not suppress.
    """
    if _line_has_trailing_marker(lines[idx]):
        return True
    if idx == 0:
        return False
    return _line_has_dedicated_marker(lines[idx - 1])


def _scan_file(
    file_path: Path,
    project_root: Path,
) -> list[str]:
    """Return a list of violation messages for *file_path*.

    Empty list means the file is clean.  Unreadable files log a warning to
    stderr and return an empty list -- this is a best-effort hook, not a
    gate, so a transient read failure should not block the Edit that
    triggered it.  The pre-push/CI gate (``check_forbidden_literals.py``)
    uses fail-closed semantics for the same code paths.
    """
    try:
        rel = file_path.relative_to(project_root).as_posix()
    except ValueError:
        # File is outside the repo root -- not in scope.
        return []

    # Only scan Python files inside ``src/synthorg/``.
    if not rel.startswith("src/synthorg/") or not rel.endswith(".py"):
        return []

    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"warning: cannot read {rel}: {exc}", file=sys.stderr)
        return []

    lines = text.splitlines()
    issues: list[str] = []

    for idx, line in enumerate(lines, start=1):
        # Per-line suppression is delegated to ``_is_suppressed`` via
        # the per-rule helpers below; we deliberately do not short-
        # circuit here on a raw substring match because that would
        # let ``x = "lint-allow: regional-defaults"`` inside a string
        # literal silence every rule on the line.
        stripped = line.lstrip()
        # Skip pure-comment lines; they discuss forbidden values, not use them.
        if stripped.startswith("#"):
            continue

        issues.extend(_scan_line_currency(rel, idx, line, lines))
        issues.extend(_scan_line_locale(rel, idx, line, lines))
        issues.extend(_scan_line_usd_suffix(rel, idx, line, lines))
        issues.extend(_scan_line_localhost(rel, idx, line, lines))

    return issues


def _scan_line_currency(
    rel: str,
    idx: int,
    line: str,
    lines: list[str],
) -> Iterable[str]:
    """Flag hardcoded ISO 4217 codes and currency-symbol-adjacent-to-digit."""
    if rel in _CURRENCY_ALLOWLIST_PATHS:
        return
    if _is_suppressed(lines, idx - 1):
        return
    for match in _HARDCODED_CURRENCY_RE.finditer(line):
        code = match.group(1)
        if code in _ISO_4217_CODES:
            yield (
                f"{rel}:{idx}: hardcoded ISO 4217 code {code!r} -- "
                "resolve from budget.currency or DEFAULT_CURRENCY"
            )
    if _HARDCODED_CURRENCY_SYMBOL_RE.search(line):
        yield (
            f"{rel}:{idx}: hardcoded currency symbol adjacent to digit -- "
            "use format_cost() or format_cost_detail()"
        )


def _scan_line_locale(
    rel: str,
    idx: int,
    line: str,
    lines: list[str],
) -> Iterable[str]:
    """Flag BCP 47 locale literals outside the allowlist."""
    if rel in _LOCALE_ALLOWLIST_PATHS:
        return
    if _is_suppressed(lines, idx - 1):
        return
    for match in _HARDCODED_LOCALE_RE.finditer(line):
        yield (
            f"{rel}:{idx}: hardcoded BCP 47 locale {match.group(1)!r} -- "
            "resolve from display.locale or pass None to Intl"
        )


def _scan_line_usd_suffix(
    rel: str,
    idx: int,
    line: str,
    lines: list[str],
) -> Iterable[str]:
    """Flag identifiers ending in ``_usd``."""
    if _is_suppressed(lines, idx - 1):
        return
    for match in _USD_SUFFIX_RE.finditer(line):
        name = match.group(0)
        yield (
            f"{rel}:{idx}: identifier {name!r} ends in '_usd' -- "
            "drop the suffix; the currency is carried on the row"
        )


def _scan_line_localhost(
    rel: str,
    idx: int,
    line: str,
    lines: list[str],
) -> Iterable[str]:
    """Flag ``localhost:<port>`` in non-allowlisted application code."""
    if rel in _LOCALHOST_ALLOWLIST_PATHS:
        return
    if _is_suppressed(lines, idx - 1):
        return
    if _LOCALHOST_PORT_RE.search(line):
        yield (
            f"{rel}:{idx}: hardcoded localhost:<port> in application code -- "
            "read from config or env (operator-configurable)"
        )


def _run_hook() -> int:
    """PostToolUse hook entry point -- reads JSON from stdin."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"warning: invalid hook JSON: {exc}", file=sys.stderr)
        return 0
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0
    return _check_one(Path(file_path))


def _check_one(file_path: Path) -> int:
    """Scan a single file; return exit code (0 clean, 1 violations).

    ``file_path`` originates from Claude Code's hook JSON payload.  Resolve
    it against the project root and refuse to proceed when the result
    escapes the repo -- silently returning 0 for out-of-scope paths keeps
    the hook contract intact while closing the path-traversal footgun
    CodeQL flagged against untrusted hook input.
    """
    project_root = Path(__file__).resolve().parent.parent
    try:
        resolved = (
            file_path if file_path.is_absolute() else project_root / file_path
        ).resolve(strict=False)
    except OSError:
        return 0
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return 0
    issues = _scan_file(resolved, project_root)
    if issues:
        for msg in issues:
            print(msg)
        return 1
    return 0


def _check_all() -> int:
    """Scan every src/synthorg/*.py file in the repo; return exit code."""
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / "src" / "synthorg"
    total = 0
    for path in sorted(src.rglob("*.py")):
        issues = _scan_file(path, project_root)
        for msg in issues:
            print(msg)
        total += len(issues)
    return 1 if total else 0


def main() -> int:
    """Dispatch CLI vs hook mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "file_path",
        nargs="?",
        type=Path,
        help="File to scan (CLI mode).  Omit to read JSON from stdin.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan every src/synthorg/*.py file.",
    )
    args = parser.parse_args()
    if args.all:
        return _check_all()
    if args.file_path is None:
        return _run_hook()
    return _check_one(args.file_path)


if __name__ == "__main__":
    sys.exit(main())
