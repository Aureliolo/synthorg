#!/usr/bin/env python3
"""Pre-push / CI gate: no silent broad-except swallows in engine/ + workers/.

Project convention (CLAUDE.md, MANDATORY "Fail-Loud Execution"): in the
execution spine (``src/synthorg/engine/`` and ``src/synthorg/workers/``)
a broad ``except Exception`` / ``except BaseException`` / bare ``except``
handler must not log-and-continue on a non-recoverable error. Either
surface the failure (``raise`` the original or a typed ``DomainError``),
or, when the swallow is a genuinely best-effort side channel
(notification, observer, heartbeat, teardown), justify it in place.

AST-based. A broad-except handler is a violation when its body contains
no ``raise`` statement and it carries no per-handler opt-out marker.

Per-handler opt-out (mandatory non-empty reason), on any line inside the
handler (the ``reraise_critical(exc)`` line is the natural anchor)::

    except Exception as exc:  # noqa: BLE001
        reraise_critical(exc)  # lint-allow: swallow-ok -- best-effort notification
        logger.warning(EVENT, ...)

Fail-closed on a syntax error. No baseline: every broad swallow in the
spine is either fail-loud or explicitly justified.

Usage::

    python scripts/check_no_engine_worker_swallow.py
    python scripts/check_no_engine_worker_swallow.py --repo-root /path/to/repo
"""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

SCAN_ROOTS: Final = (Path("src/synthorg/engine"), Path("src/synthorg/workers"))
_SUPPRESSION_MARKER: Final = "lint-allow: swallow-ok"
_BROAD_NAMES: Final = frozenset({"Exception", "BaseException"})


@dataclass(frozen=True)
class Violation:
    """One unjustified broad-except swallow in the execution spine."""

    file: str
    lineno: int
    detail: str


def _line_has_marker(line: str) -> bool:
    """Return True iff *line* carries the ``swallow-ok`` suppression marker.

    Returns:
        True when the line ends in a valid ``lint-allow: swallow-ok --``
        comment with a non-empty reason.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--") and suffix[2:].strip():
            return True
    return False


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """Return True iff *handler* catches a broad exception type.

    Returns:
        True for bare ``except:``, ``except Exception`` /
        ``except BaseException``, or a tuple including either.
    """
    node = handler.type
    if node is None:
        return True
    targets = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(isinstance(t, ast.Name) and t.id in _BROAD_NAMES for t in targets)


def _handler_raises(handler: ast.ExceptHandler) -> bool:
    """Return True iff *handler*'s body contains a ``raise`` statement.

    Returns:
        True when any ``raise`` appears in the handler subtree.
    """
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _handler_marked(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """Return True iff any source line in *handler* carries the marker.

    Returns:
        True when the opt-out marker appears on any line spanned by the
        handler.
    """
    start = handler.lineno
    end = handler.end_lineno or start
    return any(
        _line_has_marker(lines[idx])
        for idx in range(start - 1, min(end, len(lines)))
        if 0 <= idx < len(lines)
    )


def _scan_file(path: Path, repo_root: Path) -> list[Violation]:
    """Return every unjustified broad-except swallow in *path*.

    Returns:
        The violations found, empty when clean.

    Raises:
        SyntaxError: When the file cannot be parsed (fail-closed).
    """
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(repo_root).as_posix()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{path}: {exc}"
        raise SyntaxError(msg) from exc
    lines = text.splitlines()
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad(node):
            continue
        if _handler_raises(node) or _handler_marked(node, lines):
            continue
        out.append(
            Violation(
                rel,
                node.lineno,
                "broad-except swallow: neither re-raises/raises nor carries "
                "`# lint-allow: swallow-ok -- <reason>`",
            )
        )
    return out


def _iter_py_files(repo_root: Path) -> Iterable[Path]:
    for root in SCAN_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        yield from sorted(base.rglob("*.py"))


def _run(repo_root: Path) -> int:
    violations: list[Violation] = []
    for py in _iter_py_files(repo_root):
        violations.extend(_scan_file(py, repo_root))
    if not violations:
        return 0
    print("Silent broad-except swallows in the execution spine:")
    for v in sorted(violations, key=lambda x: (x.file, x.lineno)):
        print(f"  {v.file}:{v.lineno} ({v.detail})")
    print(
        "\nFix: surface the failure (raise the original or a typed "
        "DomainError), or justify a genuinely best-effort side channel "
        "with `# lint-allow: swallow-ok -- <reason>` inside the handler.",
    )
    return 1


def main() -> int:
    """CLI entry point.

    Returns:
        ``0`` clean, ``1`` on violations, ``2`` on a scan/parse error.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        return _run(args.repo_root.resolve())
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        print(f"check_no_engine_worker_swallow: scan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
