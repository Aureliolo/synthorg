"""Unit tests for the no-raw-playwright-imports convention gate."""

import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit


def _load_module() -> Any:  # type: ignore[explicit-any]  # returns dynamically loaded gate module
    src = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "check_no_raw_playwright_imports.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_check_no_raw_playwright_imports",
        src,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Any", module)  # type: ignore[explicit-any]  # dynamic gate module


def _write_pkg(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestPlaywrightGate:
    def test_clean_repo_passes(self, tmp_path: Path) -> None:
        _write_pkg(
            tmp_path,
            "src/synthorg/tools/browser/foo.py",
            "import playwright\nfrom playwright.async_api import Page\n",
        )
        _write_pkg(
            tmp_path,
            "src/synthorg/engine/bar.py",
            "x = 1\n",
        )
        module = _load_module()
        assert module._scan(tmp_path) == 0

    def test_import_playwright_outside_browser_fails(
        self,
        tmp_path: Path,
    ) -> None:
        _write_pkg(
            tmp_path,
            "src/synthorg/engine/leak.py",
            "import playwright\n",
        )
        module = _load_module()
        assert module._scan(tmp_path) == 1

    def test_from_playwright_subpath_outside_browser_fails(
        self,
        tmp_path: Path,
    ) -> None:
        _write_pkg(
            tmp_path,
            "src/synthorg/api/handler.py",
            "from playwright.async_api import async_playwright\n",
        )
        module = _load_module()
        assert module._scan(tmp_path) == 1

    def test_missing_repo_root_returns_2(self, tmp_path: Path) -> None:
        # No src/synthorg directory => exit code 2 (configuration error).
        module = _load_module()
        assert module._scan(tmp_path) == 2

    def test_multiple_imports_in_one_file_fails(self, tmp_path: Path) -> None:
        # The scanner reports every offending import (one per AST node);
        # the gate still returns 1 because at least one violation was found.
        _write_pkg(
            tmp_path,
            "src/synthorg/tools/web/leak.py",
            "import playwright\nfrom playwright.async_api import Page\n",
        )
        module = _load_module()
        assert module._scan(tmp_path) == 1
