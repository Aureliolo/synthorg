"""Pre-push / CI clock-seam gate.

Business logic MUST read time through the injected ``Clock`` seam
(``synthorg.core.clock``), never through bare stdlib calls, so tests
can drive virtual time deterministically with ``FakeClock`` and a
system clock change cannot perturb deadline / elapsed computations.

Flagged calls (the ones the acceptance criterion of WP-4 / issue
#1919 names plus the elapsed-timer ``time.time()``):

* ``time.monotonic()``
* ``time.time()``
* ``datetime.utcnow()``

``datetime.now(UTC)`` is deliberately NOT flagged: it is the
conventional aware-UTC idiom (Pydantic ``default_factory``, frozen
record stamps, ...) used hundreds of times where a Clock seam is
neither needed nor appropriate; the acceptance criterion scopes the
zero-tolerance rule to ``time.monotonic()`` / ``datetime.utcnow()``.
Classes that already own a Clock seam should still prefer
``self._clock.now()``; that is a convention reviewers enforce, not
this gate.

Whitelisted locations (the seam implementation, the observability
layer, and the sanctioned plain-callable modules documented in
``synthorg.core.clock``):

* ``src/synthorg/core/clock.py``
* anything under ``src/synthorg/observability/``
* ``communication/loop_prevention/circuit_breaker.py``
* ``communication/loop_prevention/dedup.py``
* ``communication/loop_prevention/rate_limit.py``
* ``communication/meeting/scheduler.py``

Per-line opt-out: append ``# lint-allow: clock-seam -- <reason>`` to
the offending line (or one of the two preceding lines for a decorator
/ leading comment). The justification after ``--`` is required and
must be non-empty. Use it only for genuine low-level primitives (a
sync-only thread branch that cannot await a clock, a true monotonic
primitive inside an infrastructure shim).

Usage:
    uv run python scripts/check_no_bare_time_in_business_logic.py
    uv run python scripts/check_no_bare_time_in_business_logic.py --paths src/synthorg

Exit codes:
    0 -- no violations.
    1 -- one or more violations.
    2 -- configuration error (e.g. invalid ``--repo-root``).
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_REL_SRC: Final[str] = "src/synthorg"

# Files / subtrees exempt from the rule (POSIX-relative to repo root).
_WHITELIST_FILES: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/core/clock.py",
        "src/synthorg/communication/loop_prevention/circuit_breaker.py",
        "src/synthorg/communication/loop_prevention/dedup.py",
        "src/synthorg/communication/loop_prevention/rate_limit.py",
        "src/synthorg/communication/meeting/scheduler.py",
    }
)
_WHITELIST_PREFIXES: Final[tuple[str, ...]] = ("src/synthorg/observability/",)

_OPT_OUT_MARKER: Final[str] = "# lint-allow: clock-seam --"

# (module, attr) pairs that resolve to a forbidden zero-arg call.
_FORBIDDEN_ATTR: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("time", "monotonic"),
        ("time", "time"),
        ("datetime", "utcnow"),
    }
)
# Bare names when imported via ``from time import monotonic`` etc.
_FORBIDDEN_NAME: Final[frozenset[str]] = frozenset({"monotonic", "time", "utcnow"})


class _Violation:
    __slots__ = ("line", "path", "what")

    def __init__(self, path: Path, line: int, what: str) -> None:
        self.path = path
        self.line = line
        self.what = what


def _is_whitelisted(rel_posix: str) -> bool:
    if rel_posix in _WHITELIST_FILES:
        return True
    return any(rel_posix.startswith(p) for p in _WHITELIST_PREFIXES)


def _opted_out(lines: list[str], lineno: int) -> bool:
    """True if the offending line or the two lines above carry the marker."""
    for probe in (lineno - 1, lineno - 2, lineno - 3):
        if 0 <= probe < len(lines) and _OPT_OUT_MARKER in lines[probe]:
            tail = lines[probe].split(_OPT_OUT_MARKER, 1)[1].strip()
            if tail:
                return True
    return False


def _call_label(node: ast.Call) -> str | None:
    """Return a human label if *node* is a forbidden time call, else None."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        pair = (func.value.id, func.attr)
        if pair in _FORBIDDEN_ATTR:
            return f"{pair[0]}.{pair[1]}()"
    if isinstance(func, ast.Name) and func.id in _FORBIDDEN_NAME:
        return f"{func.id}()"
    return None


def _scan_file(path: Path) -> list[_Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    lines = source.splitlines()
    out: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        label = _call_label(node)
        if label is None:
            continue
        if _opted_out(lines, node.lineno):
            continue
        out.append(_Violation(path, node.lineno, label))
    return out


def _iter_py(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            yield from sorted(root.rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    """Scan the source tree and return the gate exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Specific files / dirs to scan (default: src/synthorg).",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    if args.paths:
        roots = [Path(p).resolve() for p in args.paths]
    else:
        roots = [root / _REPO_REL_SRC]

    violations: list[_Violation] = []
    for py in _iter_py(roots):
        try:
            rel_posix = py.resolve().relative_to(root).as_posix()
        except ValueError:
            rel_posix = py.as_posix()
        if _is_whitelisted(rel_posix):
            continue
        violations.extend(_scan_file(py))

    if not violations:
        return 0

    print(
        "Clock-seam gate: bare time calls in business logic "
        f"({len(violations)} violation(s)). Inject `clock: Clock | None "
        "= None` and use `self._clock.monotonic()` / `.now()`, or add "
        "`# lint-allow: clock-seam -- <reason>` for a genuine primitive.",
        file=sys.stderr,
    )
    for v in sorted(violations, key=lambda x: (str(x.path), x.line)):
        try:
            shown = v.path.resolve().relative_to(root).as_posix()
        except ValueError:
            shown = str(v.path)
        print(f"  {shown}:{v.line}: {v.what}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
