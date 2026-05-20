#!/usr/bin/env python3
"""Pre-push / CI gate: Playwright stays bound to the browser tool.

The headless browser capability ships through one tool package
(``src/synthorg/tools/browser/``). The contract is that only that
package imports the ``playwright`` library; any other ``synthorg``
module reaching for Playwright directly is a layering violation that
makes the tool boundary leaky and the sandbox guarantees void.

This gate AST-scans ``src/synthorg/**/*.py`` and fails the build when
``import playwright`` or ``from playwright[...] import ...`` appears
outside the allowlist.

Usage::

    uv run python scripts/check_no_raw_playwright_imports.py
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

ALLOWLIST_PREFIXES: Final[tuple[str, ...]] = ("src/synthorg/tools/browser/",)


def _imports_playwright(tree: ast.AST) -> list[int]:
    """Return line numbers of any playwright import in *tree*."""
    bad: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad.extend(
                node.lineno
                for alias in node.names
                if alias.name == "playwright" or alias.name.startswith("playwright.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "playwright" or module.startswith("playwright."):
                bad.append(node.lineno)
    return bad


def _is_allowlisted(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


def _scan(root: Path) -> int:
    src = root / "src" / "synthorg"
    if not src.is_dir():
        print(f"src/synthorg not found under {root}", file=sys.stderr)
        return 2

    violations = 0
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if _is_allowlisted(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"{rel}: could not read file in playwright-import scan: {exc}",
                file=sys.stderr,
            )
            return 2
        except SyntaxError as exc:
            print(
                f"{rel}: syntax error in playwright-import scan: {exc.msg}",
                file=sys.stderr,
            )
            return 2
        for lineno in _imports_playwright(tree):
            print(
                (
                    f"{rel}:{lineno}: raw Playwright import outside "
                    "synthorg.tools.browser. Use BrowserTool instead."
                ),
                file=sys.stderr,
            )
            violations += 1

    return 1 if violations else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Path to the repo root (defaults to cwd).",
    )
    args = parser.parse_args(argv)
    return _scan(args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
