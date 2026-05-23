"""Pre-push / CI forbidden-literal gate.

Stricter counterpart to ``check_backend_regional_defaults.py``: scans every
tracked Python file under ``src/synthorg/`` for a curated list of
forbidden patterns that have no legitimate use in application code.
Designed for pre-push and GitHub Actions.

Only ``*.py`` files are scanned.  Docs are intentionally NOT scanned;
operator-facing deployment guides legitimately contain ``localhost:<port>``
examples and the occasional ``'en-US'`` / currency-code reference.

Forbidden patterns (all outside tests/, CLI Go code, and explicit allowlists):

* Identifier suffix ``_usd``
* Bare ISO 4217 currency literal in any curated code -- ``'USD'``,
  ``'EUR'``, ``'GBP'``, ``'JPY'``, etc.  Matches are gated against
  ``_ISO_4217_CODES`` so unrelated three-letter strings (HTTP methods
  like ``'GET'``, role names like ``'CEO'``, license ids) never trip
  the gate.
* Bare BCP 47 language-region tag -- ``'en-US'``, ``'de-DE'``,
  ``'fr-FR'``, etc.
* ``localhost:<port>`` references in application code

Exits non-zero with a structured list on violations.

Usage:
    python scripts/check_forbidden_literals.py
    python scripts/check_forbidden_literals.py --paths src/synthorg

Security:
    ``--paths`` arguments are resolved against the project root and
    rejected if they escape it.  This prevents the script from being
    coerced into scanning (and emitting paths for) files outside the
    repository when invoked with an attacker-controlled argv.
"""

import argparse
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final

_USD_FIELD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*_usd\b",
)
# Every pattern below is intentionally a copy of the equivalent regex
# in ``scripts/check_backend_regional_defaults.py``.  Keep them byte-
# for-byte identical: the PostToolUse hook (backend script, best-
# effort) and the pre-push / CI gate (this script, fail-closed) must
# catch the same set of violations, otherwise a Claude-edited file
# could sail through the hook and land on a branch that the gate
# then rejects minutes later, or vice versa.
_BARE_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?<![A-Z_])                 # left boundary: no identifier char
        ['"]([A-Z]{3})['"]          # quoted 3-uppercase-letter code
        (?![A-Z_])                  # right boundary""",
    re.VERBOSE,
)
# Currency symbol adjacent to digit inside a string literal (e.g.
# ``"$100"`` or ``"\u20ac50"``).  Mirrors the backend hook.
_CURRENCY_SYMBOL_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"][^'"]*                  # start of a string literal
        (?:\$|\u20ac|\u00a3|\u00a5) # common currency symbols
        \d                          # immediately followed by digit
        [^'"]*['"]""",
    re.VERBOSE,
)
# BCP 47 language-region tag (two-OR-three-letter language subtag,
# two-or-three-letter region).  The 3-letter language branch catches
# ISO 639-3 tags like ``"fil-PH"`` that the backend hook already
# matches.  Kept in sync with the backend regex.
_BARE_LOCALE_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"]                        # opening quote
        ([a-z]{2,3}                 # language subtag
        -[A-Z]{2,3})                # region subtag
        ['"]""",
    re.VERBOSE,
)
_LOCALHOST_PORT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:localhost|127\.0\.0\.1):\d+",
)
# ISO 4217 allowlist mirrored from ``check_backend_regional_defaults.py``.
# Kept deliberately in sync: both scripts decide "is this string a
# currency code?" the same way.
_ISO_4217_CODES: Final[frozenset[str]] = frozenset(
    {
        "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR",
        "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KRW",
        "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "THB", "TRY",
        "TWD", "USD", "VND", "ZAR", "BIF", "CLP", "DJF", "GNF",
        "ISK", "KMF", "MGA", "PYG", "RWF", "UGX", "VUV", "XAF",
        "XOF", "XPF", "BHD", "IQD", "JOD", "KWD", "LYD", "OMR",
        "TND",
    }
)  # fmt: skip

# ── Per-rule allowlists ────────────────────────────────────────
#
# A global allowlist blanket-exempts a file from every rule, which
# is a footgun: ``providers/presets.py`` legitimately hosts
# ``localhost:<port>`` entries for the local Ollama/LM Studio presets
# but should still be held to the currency/locale rule.  Split the
# allowlists per-rule so each exemption is a deliberate choice.
# These mirror the three allowlists in
# ``check_backend_regional_defaults.py``; keep them in sync.

# Files whose payload IS currency-code metadata -- the very thing the
# rule would otherwise flag.
_CURRENCY_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/budget/currency.py",
        "src/synthorg/budget/config.py",
        "src/synthorg/core/types.py",
        "src/synthorg/settings/definitions/budget.py",
        "src/synthorg/settings/definitions/display.py",
        "scripts/check_backend_regional_defaults.py",
        "scripts/check_forbidden_literals.py",
        "scripts/check_web_design_system.py",
        "scripts/_web_design_patterns.py",
    }
)
# Files whose payload IS locale-tag metadata.
_LOCALE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/settings/definitions/display.py",
        "scripts/check_backend_regional_defaults.py",
        "scripts/check_forbidden_literals.py",
    }
)
# Files where ``localhost:<port>`` is correct-by-design (docker DNS,
# operator-host CLI output, NATS monitoring, local-provider presets,
# API/persistence/communication default configs).
_LOCALHOST_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/api/config.py",
        "src/synthorg/communication/config.py",
        "src/synthorg/persistence/config.py",
        "src/synthorg/providers/presets.py",
        "src/synthorg/providers/discovery.py",
        "src/synthorg/settings/definitions/api.py",
        "src/synthorg/workers/__main__.py",
        "src/synthorg/memory/embedding/fine_tune_runner.py",
        "scripts/check_backend_regional_defaults.py",
        "scripts/check_forbidden_literals.py",
    }
)

_SUPPRESSION_MARKER: Final[str] = "lint-allow: regional-defaults"


def _line_has_dedicated_marker(line: str) -> bool:
    """Return True iff *line* is a whole-line marker comment.

    Matches exactly ``#`` + optional whitespace + the marker string,
    with no other text.  Rejects markers embedded in longer comments
    (e.g. ``# TODO lint-allow: regional-defaults later``) so they do
    not bleed into the following line.
    """
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    return stripped[1:].strip() == _SUPPRESSION_MARKER


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    Uses Python's :mod:`tokenize` so a ``#`` inside a string literal
    (e.g. ``x = "# lint-allow: regional-defaults"; y = "USD"``) is not
    mistaken for a comment and does not silence the ``y = "USD"``
    violation on the same line.  The marker must either exactly match
    ``_SUPPRESSION_MARKER`` or be followed by whitespace so substring
    prefixes like ``lint-allow: regional-defaults-extra`` are rejected.

    If :mod:`tokenize` fails on the line (typically an unterminated
    triple-quoted string that continues on the next line), we
    conservatively return ``False`` so the gate fails closed rather
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


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for a single file.

    Read errors (permissions, corrupt encoding) are reported as
    ``<rel>:0: unable to scan file: <cause>`` instead of being swallowed.
    A pre-push gate that fails open on unreadable files would silently
    disable enforcement -- we prefer to surface the failure.

    (noqa C901: the per-rule fan-out is linear -- splitting into
    smaller helpers would add more indirection than it removes.)
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    issues: list[str] = []
    file_lines = text.splitlines()
    currency_exempt = rel in _CURRENCY_ALLOWLIST
    locale_exempt = rel in _LOCALE_ALLOWLIST
    localhost_exempt = rel in _LOCALHOST_ALLOWLIST
    for idx, line in enumerate(file_lines, start=1):
        # Same-line suppression: trailing ``#`` comment only, never a
        # string literal occurrence.
        if _line_has_trailing_marker(line):
            continue
        # Dedicated previous-line suppression: the line above is
        # exactly ``# lint-allow: regional-defaults``.
        if idx > 1 and _line_has_dedicated_marker(file_lines[idx - 2]):
            continue
        stripped = line.lstrip()
        # Ignore pure-comment lines -- they discuss forbidden literals.
        if stripped.startswith(("#", "//")):
            continue
        # ``_usd`` suffix has no allowlist -- the suffix itself is
        # always wrong because the project removed it from every
        # model/column/type per the regional-defaults mandate.
        issues.extend(
            f"{rel}:{idx}: identifier {match.group(0)!r} ends in '_usd'"
            for match in _USD_FIELD_RE.finditer(line)
        )
        if not currency_exempt:
            for match in _BARE_CURRENCY_RE.finditer(line):
                code = match.group(1)
                if code in _ISO_4217_CODES:
                    issues.append(
                        f"{rel}:{idx}: hardcoded ISO 4217 code {code!r} "
                        "in application code"
                    )
            if _CURRENCY_SYMBOL_RE.search(line):
                issues.append(
                    f"{rel}:{idx}: hardcoded currency symbol adjacent "
                    "to digit -- use format_cost() or format_cost_detail()"
                )
        if not locale_exempt and _BARE_LOCALE_RE.search(line):
            issues.append(f"{rel}:{idx}: hardcoded BCP 47 locale literal")
        if not localhost_exempt and _LOCALHOST_PORT_RE.search(line):
            issues.append(
                f"{rel}:{idx}: hardcoded localhost:<port> in application code"
            )
    return issues


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* to an absolute path anchored under *project_root*.

    Returns ``None`` if the resolved path is outside the project root;
    the caller should treat that as a fatal argv error rather than a
    silent skip.  This is the path-traversal guard: a ``--paths ../..``
    argument is a configuration mistake, not a valid scan target.
    """
    candidate = root if root.is_absolute() else project_root / root
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return None
    return resolved


def _git_tracked_python_files(
    abs_root: Path, project_root: Path
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``.

    Delegates to ``git ls-files`` so the scanner ignores untracked
    scratch files, build artifacts, and ``.venv`` contents.  Falling
    back to ``rglob`` would flag, for example, a dev's one-off
    ``debug_usd.py`` in the project root.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        # Not a git checkout or git missing: fall back to rglob so the
        # script still works in source tarballs / CI caches without .git.
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _iter_targets(roots: list[Path], project_root: Path) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan.

    Only tracked ``*.py`` files are scanned.  Markdown docs are
    deliberately excluded (see module docstring).  Untracked and
    ignored files are excluded via ``git ls-files``.

    Per-rule allowlists live inside ``_scan_file`` -- this function
    deliberately does not skip any tracked source file, because a
    file legitimately exempt from one rule (e.g. ``providers/presets.py``
    from localhost) must still be scanned for the others.  Tests and
    CLI Go code are excluded wholesale because they are out of scope
    for the regional-defaults mandate.
    """
    targets: list[tuple[Path, str]] = []
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if rel.startswith("tests/") or "/tests/" in rel:
                continue
            if rel.startswith("cli/"):
                continue
            targets.append((path, rel))
    return targets


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/synthorg"],
        help="Roots to scan (relative to repo root).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    roots = [Path(p) for p in args.paths]
    for root in roots:
        if _resolve_root(root, project_root) is None:
            print(
                f"refusing to scan path outside project root: {root}",
                file=sys.stderr,
            )
            return 2
    total = 0
    for path, rel in _iter_targets(roots, project_root):
        issues = _scan_file(path, rel)
        for msg in issues:
            print(msg)
        total += len(issues)
    if total:
        print(
            f"\n{total} forbidden literal(s) found. "
            "Fix them or add a '# lint-allow: regional-defaults' marker "
            "if the value is legitimately demonstrative.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
