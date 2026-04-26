"""Append non-Markdown URLs to the built docs sitemap.

Zensical generates ``_site/docs/sitemap.xml`` from Markdown pages only,
so any non-Markdown asset that should appear as a search result must be
added here.

The interactive Scalar viewer (``reference.html``) is included so it is
discoverable by search engines as a landing page for "SynthOrg REST API"
queries. The raw OpenAPI schema (``openapi.json``) is intentionally
excluded because Google does not render raw JSON in search results;
including it produced permanent "Discovered, currently not indexed"
noise in Search Console without any discoverability benefit.

Exclusion is implemented by **omission from** ``EXTRA_PATHS`` below;
there is no explicit deny-list. To opt a non-Markdown asset out of the
sitemap, simply leave it out of the tuple.

Run after ``zensical build``:

    uv run python scripts/export_openapi.py
    uv run zensical build
    uv run python scripts/patch_sitemap.py

CI (``pages.yml``, ``pages-preview.yml``) runs the same sequence before
deploying the docs site.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

REPO_ROOT = Path(__file__).resolve().parent.parent
SITEMAP_FILE = REPO_ROOT / "_site" / "docs" / "sitemap.xml"

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
URLSET_TAG = f"{{{SITEMAP_NS}}}urlset"
URL_TAG = f"{{{SITEMAP_NS}}}url"
LOC_TAG = f"{{{SITEMAP_NS}}}loc"

# Non-Markdown assets to append to the sitemap. Paths are relative to
# the docs root URL, which is derived at runtime from the existing
# sitemap entries so the script stays in sync with whatever `site_url`
# mkdocs.yml resolves to (production, staging, PR previews).
#
# Add an asset here only if it is a real landing page that benefits
# from search-engine discovery. Do NOT add raw data files (``.json``,
# ``.yaml``, etc.) -- Google does not render those in SERP and listing
# them creates permanent "Discovered, currently not indexed" noise in
# Search Console (see module docstring for the openapi.json case).
EXTRA_PATHS: tuple[str, ...] = ("openapi/reference.html",)


def _parse_sitemap() -> ET.ElementTree | None:
    """Parse the built sitemap file, printing errors and returning ``None`` on failure."""
    if not SITEMAP_FILE.exists():
        print(
            f"Error: sitemap not found at {SITEMAP_FILE.relative_to(REPO_ROOT)}",
            file=sys.stderr,
        )
        print(
            "Run `uv run zensical build` first to generate the sitemap.",
            file=sys.stderr,
        )
        return None

    # Register the default namespace so ElementTree serialises tags as
    # `<url>` rather than `<ns0:url>`. Must be called before tree.write(),
    # not before parse -- parse is unaffected by the registry.
    ET.register_namespace("", SITEMAP_NS)

    try:
        # defusedxml guards against billion-laughs, XXE, and DTD abuse.
        # The sitemap is produced by our own zensical build step, but
        # using the hardened parser costs nothing and removes the
        # supply-chain assumption from the code.
        return DefusedET.parse(SITEMAP_FILE)
    except (ET.ParseError, DefusedXmlException) as exc:
        print(
            f"Failed to parse sitemap at {SITEMAP_FILE.relative_to(REPO_ROOT)}: {exc}",
            file=sys.stderr,
        )
        return None


def _validate_root(tree: ET.ElementTree) -> ET.Element | None:
    """Return the root element if it is a ``<urlset>``, else ``None`` after printing an error."""
    root = tree.getroot()
    if root is None or root.tag != URLSET_TAG:
        actual = "<empty tree>" if root is None else repr(root.tag)
        print(
            f"Error: sitemap root element is {actual}, expected {URLSET_TAG!r}.",
            file=sys.stderr,
        )
        print(
            "The sitemap may use a different schema version or be a "
            "sitemap index; update patch_sitemap.py to match.",
            file=sys.stderr,
        )
        return None
    return root


def _collect_existing_urls(root: ET.Element) -> set[str]:
    """Collect all non-empty ``<loc>`` text values from existing ``<url>`` entries."""
    urls = {(loc.text or "").strip() for loc in root.findall(f"{URL_TAG}/{LOC_TAG}")}
    urls.discard("")
    return urls


def _derive_base_url(existing: set[str]) -> str | None:
    """Derive the docs root URL from existing sitemap entries.

    The shortest existing URL is the docs root (index.md always maps to
    the shortest path in a mkdocs-generated sitemap). Returns ``None``
    after printing an error if no entries exist.
    """
    if not existing:
        print(
            "Error: sitemap has no <url> entries; cannot derive base URL.",
            file=sys.stderr,
        )
        return None
    return min(existing, key=len).rstrip("/")


def _add_extra_urls(root: ET.Element, existing: set[str], base_url: str) -> int:
    """Append missing ``EXTRA_PATHS`` entries under ``root`` and return how many were added."""
    added = 0
    for path in EXTRA_PATHS:
        url = f"{base_url}/{path}"
        if url in existing:
            print(f"  skip (already present): {url}")
            continue
        url_elem = ET.SubElement(root, URL_TAG)
        loc_elem = ET.SubElement(url_elem, LOC_TAG)
        loc_elem.text = url
        added += 1
        print(f"  added: {url}")
    return added


def _write_sitemap(tree: ET.ElementTree, added: int) -> int:
    """Write the patched tree back to disk. Returns 0 on success, 1 on OSError."""
    # ET.indent keeps the file diff-friendly if it ever needs a human read.
    ET.indent(tree, space="  ")
    try:
        tree.write(SITEMAP_FILE, encoding="utf-8", xml_declaration=True)
    except OSError as exc:
        print(
            f"Failed to write patched sitemap to "
            f"{SITEMAP_FILE.relative_to(REPO_ROOT)}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(f"Wrote {added} extra URL(s) to {SITEMAP_FILE.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    """Patch the built sitemap to include the static OpenAPI artifact URLs.

    Idempotent: on rerun (when all URLs are already present) the file is
    not rewritten. Returns 1 if the sitemap file is missing, malformed,
    has an unexpected root element, has no entries to derive a base URL
    from, or cannot be written back; 0 otherwise.
    """
    tree = _parse_sitemap()
    if tree is None:
        return 1
    root = _validate_root(tree)
    if root is None:
        return 1
    existing = _collect_existing_urls(root)
    base_url = _derive_base_url(existing)
    if base_url is None:
        return 1
    added = _add_extra_urls(root, existing, base_url)
    if added == 0:
        print("Sitemap already contains all extra URLs; no changes written.")
        return 0
    return _write_sitemap(tree, added)


if __name__ == "__main__":
    sys.exit(main())
