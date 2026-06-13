#!/usr/bin/env python3
"""Gate: every ``docs/`` page is reachable from the mkdocs nav or allowlisted.

Walks the ``nav:`` tree in ``mkdocs.yml``, collects every referenced
markdown path, globs ``docs/**/*.md``, and fails when an on-disk page is
neither in the nav nor on the documented internal-only allowlist. This
prevents nav drift in both directions: a new page silently absent from
navigation, and a nav entry pointing at a file that no longer exists.

The allowlist is for genuinely internal / point-in-time dev notes that
should not appear in the published navigation. Each entry carries a
one-line reason. Promote a page into the nav rather than expanding this
list once it becomes user- or contributor-facing.

Exit codes:
    0 - every disk page is in nav or allowlisted; every nav entry resolves
        to a file on disk; the allowlist is clean.
    1 - one or more coverage problems printed to stderr.
"""

import sys
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import yaml

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DOCS_DIR: Final[Path] = REPO_ROOT / "docs"
MKDOCS_FILE: Final[Path] = REPO_ROOT / "mkdocs.yml"

# Pages intentionally excluded from the published nav. Key is the
# docs-relative POSIX path; value is the justification. Keep this list
# minimal: a contributor-facing page belongs in the nav, not here.
ALLOWLIST: Final[dict[str, str]] = {
    "DESIGN_SPEC.md": (
        "design-spec source-of-truth index; duplicates the Design nav section"
    ),
    "reference/web-base-ui-decisions.md": "internal web-dashboard dev note",
    "reference/web-design-system.md": "internal web-dashboard dev note",
    "reference/web-package-structure.md": "internal web-dashboard dev note",
    "reference/web-post-training.md": "internal web-dashboard dev note",
    "reference/web-zustand-stores.md": "internal web-dashboard dev note",
    "reference/py314-flake-investigation-2026-05.md": (
        "point-in-time CPython flake investigation"
    ),
    "reference/rl-consolidation-feasibility.md": "internal feasibility study",
}


class _NavLoader(yaml.SafeLoader):
    """SafeLoader that tolerates mkdocs' custom YAML tags.

    ``mkdocs.yml`` carries ``!ENV`` and ``!!python/name:`` tags (used by
    the material theme and pymdownx). The nav itself is plain data, so
    those two tags resolve to ``None`` instead of failing the parse; any
    other unknown tag still raises, surfacing genuinely malformed config.
    """


def _ignore_unknown(loader: object, suffix: object, node: object) -> None:
    """Resolve any python/name-tagged node to ``None`` (implicit return)."""


def _ignore_env(loader: object, node: object) -> None:
    """Resolve the ``!ENV`` tag to ``None`` (implicit return)."""


_NavLoader.add_multi_constructor(  # type: ignore[no-untyped-call]
    "tag:yaml.org,2002:python/name:", _ignore_unknown
)
_NavLoader.add_constructor("!ENV", _ignore_env)


def _collect_nav_md(node: object, out: set[str]) -> None:
    """Recursively collect every local ``.md`` reference in the nav tree.

    Nav strings are normalised to the canonical disk form (forward
    slashes, no leading ``./``) so the set comparison in ``main`` does not
    raise a false ``missing from nav`` on a ``./``-prefixed entry or a
    Windows-style backslash-separated path. External links (anything with
    a URL scheme or host, e.g. ``https://...``) are skipped -- they are
    not local pages.
    """
    if isinstance(node, str):
        parsed = urlsplit(node)
        if parsed.scheme or parsed.netloc:
            return
        rel = node.replace("\\", "/")
        rel = rel.removeprefix("./")
        if rel.endswith(".md"):
            out.add(rel)
    elif isinstance(node, list):
        for item in node:
            _collect_nav_md(item, out)
    elif isinstance(node, dict):
        for value in node.values():
            _collect_nav_md(value, out)


def _nav_paths() -> set[str]:
    """Return the set of docs-relative ``.md`` paths referenced in the nav.

    Raises ``RuntimeError`` (not a raw traceback) when ``mkdocs.yml`` is
    unreadable, malformed YAML, or missing a ``nav`` mapping, so ``main``
    can print a clean ``error:`` line and exit 1.
    """
    # _NavLoader subclasses SafeLoader; the custom tags resolve to None, so
    # no arbitrary object construction is possible.
    try:
        text = MKDOCS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"could not read {MKDOCS_FILE}: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc
    try:
        loaded = yaml.load(text, Loader=_NavLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        msg = f"{MKDOCS_FILE.name} is not valid YAML: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(loaded, dict) or "nav" not in loaded:
        msg = "mkdocs.yml has no 'nav' mapping"
        raise RuntimeError(msg)
    out: set[str] = set()
    _collect_nav_md(loaded["nav"], out)
    return out


def _disk_paths() -> set[str]:
    """Return every docs-relative ``.md`` path on disk.

    Raises ``RuntimeError`` on a filesystem walk error so ``main`` can
    report it cleanly rather than crashing with a traceback.
    """
    try:
        return {
            path.relative_to(DOCS_DIR).as_posix() for path in DOCS_DIR.rglob("*.md")
        }
    except OSError as exc:
        msg = f"could not walk {DOCS_DIR}: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc


def main() -> int:
    """Check nav coverage; return shell exit code."""
    try:
        nav = _nav_paths()
        disk = _disk_paths()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    allowlisted = set(ALLOWLIST)

    problems: list[str] = []

    # On-disk pages missing from both nav and allowlist.
    problems.extend(
        f"{rel}: on disk but not in mkdocs.yml nav and not allowlisted. "
        "Add it to the nav, or add an allowlist entry (with a reason) in "
        "scripts/check_docs_nav_coverage.py."
        for rel in sorted(disk - nav - allowlisted)
    )

    # Nav entries pointing at files that do not exist.
    problems.extend(
        f"{rel}: referenced in mkdocs.yml nav but not present under docs/."
        for rel in sorted(nav - disk)
    )

    # Allowlist hygiene: every entry must exist on disk and stay out of nav.
    for rel in sorted(allowlisted):
        if rel not in disk:
            problems.append(
                f"{rel}: allowlisted in check_docs_nav_coverage.py but absent "
                "from docs/. Remove the stale allowlist entry."
            )
        elif rel in nav:
            problems.append(
                f"{rel}: both allowlisted and present in the nav. Remove the "
                "allowlist entry; the page is already navigable."
            )

    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        print(
            f"\n{len(problems)} docs-nav coverage problem(s). Every docs/ page "
            "must be in the mkdocs nav or on the documented internal allowlist.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(disk)} docs page(s) all reachable from nav "
        f"({len(allowlisted)} allowlisted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
