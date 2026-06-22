"""Tests for scripts/generate_docs_redirects.py.

Pins the redirect-stub generator's contract:

* source-path -> directory-URL mapping (index, nested index, normal, no prefix)
* a ``../`` traversal in a redirect key is rejected (no write outside site_dir)
* a disallowed target scheme (``javascript:``) is rejected; HTML is escaped
* ``redirect_maps`` extraction tolerates a malformed plugins block
* the full ``main()`` round-trip writes one stub per entry into site_dir
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _import_script(name: str) -> ModuleType:
    """Load ``scripts/<name>.py`` as a module, mirroring the sibling tests."""
    script = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _import_script("generate_docs_redirects")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("doc_path", "prefix", "expected"),
    [
        ("index.md", "/docs", "/docs/"),
        ("guides/index.md", "/docs", "/docs/guides/"),
        ("reference/comparison.md", "/docs", "/docs/reference/comparison/"),
        ("getting-started.md", "", "/getting-started/"),
    ],
)
def test_doc_to_url(doc_path: str, prefix: str, expected: str) -> None:
    """Source paths map to their canonical directory URL."""
    assert gen._doc_to_url(doc_path, prefix) == expected


@pytest.mark.unit
def test_doc_to_stub_dir_normal(tmp_path: Path) -> None:
    """A normal redirect key resolves to a directory inside site_dir."""
    result = gen._doc_to_stub_dir(tmp_path, "getting-started.md")
    assert result == (tmp_path / "getting-started").resolve()


@pytest.mark.unit
def test_doc_to_stub_dir_traversal_raises(tmp_path: Path) -> None:
    """A ``../`` traversal key escapes site_dir and is rejected."""
    with pytest.raises(ValueError, match="subpath"):
        gen._doc_to_stub_dir(tmp_path, "../../escape.md")


@pytest.mark.unit
def test_safe_target_allows_https_and_relative() -> None:
    """https and site-relative targets pass through (HTML-escaped)."""
    assert gen._safe_target("https://example.com/x/") == "https://example.com/x/"
    relative = "/docs/reference/comparison/"
    assert gen._safe_target(relative) == relative


@pytest.mark.unit
def test_safe_target_rejects_javascript_scheme() -> None:
    """A javascript: target must never reach the meta-refresh stub."""
    with pytest.raises(ValueError, match="disallowed scheme"):
        gen._safe_target("javascript:alert(1)")


@pytest.mark.unit
def test_safe_target_escapes_html() -> None:
    """HTML-special characters in a target are escaped for attribute context."""
    assert "&quot;" in gen._safe_target('/x"y')


@pytest.mark.unit
def test_redirect_maps_tolerates_malformed_plugins() -> None:
    """A non-list plugins block yields an empty map rather than raising."""
    assert gen._redirect_maps({"plugins": None}) == {}
    assert gen._redirect_maps({}) == {}


@pytest.mark.unit
def test_main_round_trip(tmp_path: Path) -> None:
    """main() writes one stub per redirect entry into the built site_dir."""
    site_dir = tmp_path / "_site" / "docs"
    site_dir.mkdir(parents=True)
    config = {
        "site_dir": "_site/docs",
        "site_url": "https://example.com/docs/",
        "plugins": [{"redirects": {"redirect_maps": {"old-page.md": "new/page.md"}}}],
    }
    with (
        patch.object(gen, "_REPO_ROOT", tmp_path),
        patch.object(gen, "_load_config", lambda: config),
    ):
        assert gen.main() == 0
    stub = site_dir / "old-page" / "index.html"
    assert stub.is_file()
    body = stub.read_text(encoding="utf-8")
    assert "/docs/new/page/" in body
