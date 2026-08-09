"""Tests for scripts/patch_docs_a11y.py.

Pins the drawer-overlay patcher's contract:

* the overlay's ``aria-label`` is stripped whatever the attribute order
* the header's ``md-header__button`` labels keep theirs, because there the
  attribute is the only accessible name those icon-only controls have
* the rewrite is idempotent, so a rerun over patched output is a no-op
* ``main()`` walks the built tree and reports how many pages it touched
"""

import importlib.util
from pathlib import Path
from types import ModuleType

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


patcher = _import_script("patch_docs_a11y")

HEADER_LABEL = (
    '<label class="md-header__button md-icon" for="__drawer" aria-label="Navigation">'
)
OVERLAY = '<label class="md-overlay" for="__drawer" aria-label="Navigation"></label>'


@pytest.mark.unit
@pytest.mark.parametrize(
    "tag",
    [
        '<label class="md-overlay" for="__drawer" aria-label="Navigation">',
        '<label aria-label="Navigation" class="md-overlay" for="__drawer">',
        "<label class='md-overlay' for='__drawer' aria-label='Navigation'>",
        '<LABEL CLASS="md-overlay" ARIA-LABEL="Navigation">',
    ],
)
def test_strips_overlay_aria_label_whatever_the_attribute_order(tag: str) -> None:
    patched, removed = patcher._strip_overlay_label(tag)

    assert removed == 1
    assert "aria-label" not in patched.lower()
    assert "md-overlay" in patched.lower()


@pytest.mark.unit
def test_leaves_header_button_labels_named() -> None:
    """The header icons have no text, so their aria-label is load-bearing."""
    patched, removed = patcher._strip_overlay_label(HEADER_LABEL)

    assert removed == 0
    assert patched == HEADER_LABEL


@pytest.mark.unit
def test_patches_only_the_overlay_in_a_full_page() -> None:
    html = (
        f"<body>{HEADER_LABEL}</label>"
        '<label class="md-overlay" for="__drawer" aria-label="Navigation"></label>'
        "</body>"
    )

    patched, removed = patcher._strip_overlay_label(html)

    assert removed == 1
    assert patched.count('aria-label="Navigation"') == 1
    assert 'class="md-overlay" for="__drawer"></label>' in patched


@pytest.mark.unit
def test_rewrite_is_idempotent() -> None:
    once, first = patcher._strip_overlay_label(
        '<label class="md-overlay" for="__drawer" aria-label="Navigation">'
    )
    twice, second = patcher._strip_overlay_label(once)

    assert first == 1
    assert second == 0
    assert twice == once


@pytest.mark.unit
def test_main_walks_the_built_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "_site" / "docs"
    (docs / "guides").mkdir(parents=True)
    (docs / "index.html").write_text(OVERLAY, encoding="utf-8")
    (docs / "guides" / "index.html").write_text(OVERLAY, encoding="utf-8")
    (docs / "guides" / "untouched.html").write_text(HEADER_LABEL, encoding="utf-8")
    monkeypatch.setattr(patcher, "DOCS_DIR", docs)

    assert patcher.main() == 0

    nested = docs / "guides" / "index.html"
    assert "aria-label" not in (docs / "index.html").read_text(encoding="utf-8")
    assert "aria-label" not in nested.read_text(encoding="utf-8")
    untouched = docs / "guides" / "untouched.html"
    assert untouched.read_text(encoding="utf-8") == HEADER_LABEL


@pytest.mark.unit
def test_main_fails_loudly_when_the_build_output_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(patcher, "DOCS_DIR", tmp_path / "absent")

    assert patcher.main() == 1


@pytest.mark.unit
def test_main_fails_loudly_when_the_build_output_has_no_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "_site" / "docs"
    empty.mkdir(parents=True)
    monkeypatch.setattr(patcher, "DOCS_DIR", empty)

    assert patcher.main() == 1
