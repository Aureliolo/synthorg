#!/usr/bin/env python3
"""Pre-push / CI gate forbidding controller-level `except`-and-`Response()`.

Enforces the rule documented in
``docs/reference/errors.md`` -- "HTTP exception handler registration":
controllers under ``src/synthorg/api/controllers/`` MUST NOT catch a
domain error and build their own ``Response(...)`` envelope. Domain
errors must propagate so the centralised handlers in
``src/synthorg/api/exception_handlers.py::EXCEPTION_HANDLERS`` produce
the RFC 9457 envelope.

What's flagged:

    try:
        ...
    except SomeError:                 # name ends in "Error"
        return Response(...)          # built locally; should be raise

What's allowed:

    except InvalidCursorError:
        raise                         # bare re-raise

    except PersistenceVersionConflictError as exc:
        msg = "..."
        raise VersionConflictError(msg) from exc  # typed re-raise

    except OSError:                   # stdlib base class -- not a
        return Response(...)          # domain error; allowed

Scope is narrow on purpose: only ``src/synthorg/api/controllers/``;
other layers may still catch-and-respond for their own reasons (and
the broader audit will sweep those separately).

Per-line opt-out::

    return Response(...)  # lint-allow: controller-domain-response -- <reason>

The justification after ``--`` is required and must be non-empty.

A frozen baseline file
(``scripts/no_controller_response_for_domain_errors_baseline.txt``)
lists pre-existing violations the gate should ignore so this PR can
land while the longer-tail clean-up tracks separately. Entries are
``<posix_path>:<lineno>:<except_clause_name>``. The baseline is
expected to shrink monotonically; a stale entry is reported as drift.

Usage::

    uv run python scripts/check_no_controller_response_for_domain_errors.py
    uv run python scripts/check_no_controller_response_for_domain_errors.py \
        --update-baseline
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Final

_SCAN_REL: Final[str] = "src/synthorg/api/controllers"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*controller-domain-response\s*--\s*\S",
)

_BASELINE_REL: Final[str] = (
    "scripts/no_controller_response_for_domain_errors_baseline.txt"
)

_BASELINE_HEADER: Final[str] = """\
# Frozen baseline of pre-existing controller-level `except <DomainError>:
# return Response(...)` patterns under src/synthorg/api/controllers/.
# Each line is `path:lineno:except_clause_name` (POSIX path, 1-indexed
# line) sorted in deterministic order.
#
# scripts/check_no_controller_response_for_domain_errors.py reads this
# file to suppress violations at these exact entries. New violations
# NOT in this list will fail the pre-push hook.
#
# This file should shrink monotonically. Regenerate (rare; requires
# explicit user approval) with:
#   uv run python scripts/check_no_controller_response_for_domain_errors.py \\
#       --update-baseline
"""

_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^.+:\d+:.+$")

# Stdlib bases that are NOT domain errors. Catching these locally and
# building a Response is fine -- they are not part of the centralised
# domain-error pipeline.
_STDLIB_BASES: Final[frozenset[str]] = frozenset(
    {
        "Exception",
        "BaseException",
        "RuntimeError",
        "LookupError",
        "PermissionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "OSError",
        "IOError",
        "ImportError",
        "asyncio.CancelledError",
        "asyncio.TimeoutError",
        "MemoryError",
        "RecursionError",
    }
)


def _name_of(node: ast.expr | None) -> str:
    """Return a dotted name for an ``ast`` expression, or empty string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name_of(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _excepts_for(handler: ast.ExceptHandler) -> tuple[str, ...]:
    """Return the dotted names listed in an ``except`` clause."""
    if handler.type is None:
        return ()
    if isinstance(handler.type, ast.Tuple):
        return tuple(_name_of(elt) for elt in handler.type.elts)
    return (_name_of(handler.type),)


def _is_domain_error_name(name: str) -> bool:
    """Return True iff ``name`` looks like a project domain error class.

    Heuristic: ends with ``Error`` and is not one of the known stdlib
    or third-party non-domain bases. The check is intentionally
    permissive -- the audit-level review still validates that any
    flagged class is actually a ``DomainError`` subtype before
    requiring action.
    """
    if not name.endswith("Error"):
        return False
    return name not in _STDLIB_BASES


def _builds_response_envelope(node: ast.AST) -> bool:
    """Return True iff *node* is ``return Response(...)``.

    Matches both the unqualified ``Response(...)`` and any dotted
    attribute access whose terminal identifier is ``Response`` (e.g.
    ``litestar.Response(...)``), so an alias-imported envelope still
    trips the gate.
    """
    if not isinstance(node, ast.Return) or node.value is None:
        return False
    call = node.value
    if not isinstance(call, ast.Call):
        return False
    func_name = _name_of(call.func)
    return func_name == "Response" or func_name.endswith(".Response")


def _line_has_suppression(source_lines: list[str], lineno: int) -> bool:
    """Return True iff the line at *lineno* (1-indexed) carries the marker."""
    if lineno < 1 or lineno > len(source_lines):
        return False
    return bool(_SUPPRESSION_RE.search(source_lines[lineno - 1]))


def _find_violations(
    tree: ast.AST,
    source_lines: list[str],
) -> list[tuple[int, str, str]]:
    """Return ``(lineno, except_clause_name, message)`` for every violation."""
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            domain_names = tuple(
                n for n in _excepts_for(handler) if _is_domain_error_name(n)
            )
            if not domain_names:
                continue
            for child in ast.walk(handler):
                if not isinstance(child, ast.Return):
                    continue
                if not _builds_response_envelope(child):
                    continue
                if _line_has_suppression(source_lines, child.lineno):
                    continue
                names = ", ".join(domain_names)
                msg = (
                    f"controller catches {names} and builds "
                    f"a Response envelope locally; raise the typed domain "
                    f"error and let the centralised handler in "
                    f"src/synthorg/api/exception_handlers.py produce the "
                    f"RFC 9457 envelope (see docs/reference/errors.md)"
                )
                findings.append((child.lineno, names, msg))
    return findings


def _load_baseline(repo_root: Path) -> set[tuple[str, int, str]]:
    """Return parsed baseline entries as ``(rel_path, lineno, names)``."""
    baseline_path = repo_root / _BASELINE_REL
    if not baseline_path.exists():
        return set()
    entries: set[tuple[str, int, str]] = set()
    for raw in baseline_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _BASELINE_ENTRY_RE.match(line):
            print(
                f"{baseline_path}: malformed baseline entry: {line!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        rel_path, lineno_str, names = line.rsplit(":", 2)
        entries.add((rel_path, int(lineno_str), names))
    return entries


def _write_baseline(
    repo_root: Path,
    findings: list[tuple[Path, int, str]],
) -> None:
    """Write *findings* to the baseline file in deterministic order."""
    baseline_path = repo_root / _BASELINE_REL
    lines = sorted(
        f"{p.relative_to(repo_root).as_posix()}:{lineno}:{names}"
        for p, lineno, names in findings
    )
    body = _BASELINE_HEADER + "\n".join(lines) + ("\n" if lines else "")
    baseline_path.write_text(body, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    """Parse the gate's command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline file with the current findings.",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Ignore the baseline file (treat every finding as a violation).",
    )
    return parser.parse_args()


def _scan_findings(
    scan_root: Path,
) -> list[tuple[Path, int, str, str]] | int:
    """Scan *scan_root* and return findings (or an exit code on failure)."""
    findings: list[tuple[Path, int, str, str]] = []
    for py_path in sorted(scan_root.rglob("*.py")):
        source = py_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError as exc:
            print(f"{py_path}: failed to parse: {exc}", file=sys.stderr)
            return 1
        for lineno, names, message in _find_violations(
            tree,
            source.splitlines(),
        ):
            findings.append((py_path, lineno, names, message))
    return findings


def _report_violations(
    scan_root: Path,
    repo_root: Path,
    violations: list[tuple[Path, int, str]],
) -> None:
    """Print *violations* with a remediation hint."""
    rel_root = scan_root.relative_to(repo_root)
    print(
        f"\n{len(violations)} controller-level domain-error response(s) "
        f"in {rel_root.as_posix()}/ (excluding baseline):\n",
        file=sys.stderr,
    )
    for py_path, lineno, message in violations:
        rel = py_path.relative_to(repo_root).as_posix()
        print(f"  {rel}:{lineno}: {message}", file=sys.stderr)
    print(
        "\nRefactor: declare ClassVars (status_code, error_code, "
        "error_category) on the domain error and `raise` (or "
        "`raise X(...) from exc`) instead of catching locally.",
        file=sys.stderr,
    )


def _report_stale_baseline(
    stale_baseline: list[tuple[str, int, str]],
) -> None:
    """Print *stale_baseline* entries with a regeneration hint."""
    print(
        f"\n{len(stale_baseline)} baseline entries no longer match a "
        f"violation -- shrink the baseline:\n",
        file=sys.stderr,
    )
    for rel, lineno, names in stale_baseline:
        print(f"  {rel}:{lineno}: {names}", file=sys.stderr)
    print(
        "\nRegenerate with: uv run python scripts/"
        "check_no_controller_response_for_domain_errors.py "
        "--update-baseline",
        file=sys.stderr,
    )


def main() -> int:
    """Walk controllers, report violations, exit 1 on any finding."""
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    scan_root = repo_root / _SCAN_REL
    if not scan_root.exists():
        print(f"controller scan root not found: {scan_root}", file=sys.stderr)
        return 1

    findings = _scan_findings(scan_root)
    if isinstance(findings, int):
        return findings

    if args.update_baseline:
        _write_baseline(
            repo_root,
            [(p, lineno, names) for p, lineno, names, _msg in findings],
        )
        print(
            f"baseline written with {len(findings)} entries to "
            f"{(repo_root / _BASELINE_REL).relative_to(repo_root).as_posix()}",
        )
        return 0

    baseline = set() if args.no_baseline else _load_baseline(repo_root)

    violations: list[tuple[Path, int, str]] = []
    seen_in_findings: set[tuple[str, int, str]] = set()
    for py_path, lineno, names, message in findings:
        rel = py_path.relative_to(repo_root).as_posix()
        seen_in_findings.add((rel, lineno, names))
        if (rel, lineno, names) in baseline:
            continue
        violations.append((py_path, lineno, message))

    stale_baseline = sorted(baseline - seen_in_findings)
    if stale_baseline:
        _report_stale_baseline(stale_baseline)
        return 1

    if not violations:
        return 0

    _report_violations(scan_root, repo_root, violations)
    return 1


if __name__ == "__main__":
    sys.exit(main())
