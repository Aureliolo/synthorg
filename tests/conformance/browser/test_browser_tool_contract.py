"""Conformance tests for the headless browser tool.

These verify the structural contracts (category + action types
registered, persistence boundary respected, manifest discipline)
that the rest of the codebase relies on.
"""

import ast
from pathlib import Path

import pytest

from synthorg.security.action_type_mapping import DEFAULT_CATEGORY_ACTION_MAP
from synthorg.security.action_types import (
    ActionTypeCategory,
    ActionTypeRegistry,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory

pytestmark = pytest.mark.unit


class TestRegistration:
    def test_tool_category_browser_exists(self) -> None:
        assert ToolCategory.BROWSER.value == "browser"

    def test_browser_action_types_present(self) -> None:
        for action in (
            ActionType.BROWSER_NAVIGATE,
            ActionType.BROWSER_SCREENSHOT,
            ActionType.BROWSER_DIFF,
            ActionType.BROWSER_ACCESSIBILITY_SCAN,
            ActionType.BROWSER_SPEC,
        ):
            assert action.value.startswith("browser:")

    def test_browser_category_enum_added(self) -> None:
        assert ActionTypeCategory.BROWSER.value == "browser"

    def test_default_category_action_map_browser_entry(self) -> None:
        assert (
            DEFAULT_CATEGORY_ACTION_MAP[ToolCategory.BROWSER]
            is ActionType.BROWSER_NAVIGATE
        )

    def test_registry_validates_browser_actions(self) -> None:
        registry = ActionTypeRegistry()
        for action in (
            ActionType.BROWSER_NAVIGATE,
            ActionType.BROWSER_SCREENSHOT,
            ActionType.BROWSER_DIFF,
            ActionType.BROWSER_ACCESSIBILITY_SCAN,
            ActionType.BROWSER_SPEC,
        ):
            registry.validate(action.value)


class TestPersistenceBoundary:
    """Ensure tools/browser/ has no sqlite/psycopg imports."""

    _FORBIDDEN_ROOTS = frozenset(
        {"sqlite3", "aiosqlite", "psycopg", "psycopg_pool"},
    )

    def _module_root(self, name: str | None) -> str:
        if not name:
            return ""
        return name.split(".")[0]

    def test_no_persistence_imports(self) -> None:
        package = Path(__file__).resolve().parents[3] / ("src/synthorg/tools/browser")
        offenders: list[str] = []
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders.extend(
                        f"{path}:{node.lineno} {alias.name}"
                        for alias in node.names
                        if self._module_root(alias.name) in self._FORBIDDEN_ROOTS
                    )
                elif isinstance(node, ast.ImportFrom) and (
                    self._module_root(node.module) in self._FORBIDDEN_ROOTS
                ):
                    offenders.append(
                        f"{path}:{node.lineno} {node.module}",
                    )
        assert not offenders, (
            "tools/browser/ must not import persistence libraries directly; "
            f"violations: {offenders}"
        )


class TestManifestEntries:
    def test_browser_symbols_marked_enforced(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        manifest = repo_root / "scripts" / "_ghost_wiring_manifest.txt"
        text = manifest.read_text(encoding="utf-8")
        required = (
            "BrowserTool",
            "SSIMDiffer",
            "WorkspaceBaselineStore",
        )
        for symbol in required:
            line = next(
                (
                    raw
                    for raw in text.splitlines()
                    if raw.strip().startswith("ENFORCED") and symbol in raw
                ),
                None,
            )
            assert line is not None, (
                f"Manifest is missing ENFORCED entry for {symbol!r}"
            )
