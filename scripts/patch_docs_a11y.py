"""Strip the prohibited ``aria-label`` from the built docs drawer overlay.

The theme emits ``<label class="md-overlay" for="__drawer" aria-label="...">``
as a full-page click-catcher that closes the navigation drawer. axe-core 4.13.0
(``aria-prohibited-attr``) rejects naming a ``<label>``: a label takes its
accessible name from its content, so an ``aria-label`` on one is invalid ARIA
and the element is reported as a violation.

Removing the name is the correct resolution rather than adding a role. The
overlay is a redundant pointer target for a control that already exists in the
header (the drawer toggle carries its own name), so announcing it a second time
would add a duplicate control to the accessibility tree rather than remove one.

Only ``md-overlay`` is touched. The header's ``md-header__button`` labels also
carry ``aria-label``, and there the attribute is load-bearing: those wrap an
icon with no text, so stripping it would leave two unnamed controls. Widening
this to every ``<label>`` would trade one violation for two worse ones.

Run after ``zensical build``:

    uv run zensical build
    uv run python scripts/patch_docs_a11y.py

CI (``build-docs.yml``, ``build-docs-preview.yml``) runs the same sequence
before deploying the docs site.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "_site" / "docs"

# Matches the overlay's opening tag and captures the ``aria-label`` attribute
# so it can be dropped. Attribute order is not fixed by the template, so the
# class check and the attribute capture are separate lookaheads rather than one
# positional pattern.
OVERLAY_TAG = re.compile(
    r"<label(?=[^>]*\bclass=[\"'][^\"']*\bmd-overlay\b)[^>]*>",
    re.IGNORECASE,
)
ARIA_LABEL_ATTR = re.compile(r"\s+aria-label=(\"[^\"]*\"|'[^']*')", re.IGNORECASE)


def _strip_overlay_label(html: str) -> tuple[str, int]:
    """Return ``html`` with the overlay's ``aria-label`` removed, and a count."""
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        tag = match.group(0)
        cleaned, count = ARIA_LABEL_ATTR.subn("", tag)
        removed += count
        return cleaned

    return OVERLAY_TAG.sub(replace, html), removed


def main() -> int:
    """Patch every built docs page, returning 1 if the build output is missing."""
    if not DOCS_DIR.is_dir():
        print(f"Error: built docs not found at {DOCS_DIR}", file=sys.stderr)
        print("Run `uv run zensical build` first.", file=sys.stderr)
        return 1

    pages = sorted(DOCS_DIR.rglob("*.html"))
    if not pages:
        print(f"Error: no HTML pages under {DOCS_DIR}", file=sys.stderr)
        return 1

    patched_pages = 0
    total_removed = 0
    for page in pages:
        original = page.read_text(encoding="utf-8")
        patched, removed = _strip_overlay_label(original)
        if removed == 0:
            continue
        page.write_text(patched, encoding="utf-8")
        patched_pages += 1
        total_removed += removed

    print(
        f"Removed {total_removed} prohibited aria-label(s) "
        f"across {patched_pages}/{len(pages)} page(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
