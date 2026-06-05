"""Unit tests for AST-based semantic conflict checks.

Tests the pure check functions in semantic_checks.py with
source code strings. Each check category has multiple test
cases covering positive detections and negative (no-conflict) cases.
"""

import pytest

from synthorg.core.enums import ConflictType
from synthorg.engine.workspace.semantic_checks import (
    check_duplicate_definitions,
    check_import_conflicts,
    check_removed_references,
)
from synthorg.engine.workspace.semantic_checks_signatures import (
    check_signature_changes,
)

pytestmark = pytest.mark.unit


# -------------------------------------------------------------------
# check_removed_references
# -------------------------------------------------------------------


class TestCheckRemovedReferences:
    """Tests for detecting references to names removed by merge."""

    @pytest.mark.parametrize(
        (
            "base_sources",
            "merged_sources",
            "expected_min",
            "description_contains",
        ),
        [
            pytest.param(
                {
                    "utils.py": (
                        "def calculate_total(items):\n    return sum(items)\n"
                    ),
                },
                {
                    "utils.py": ("def compute_total(items):\n    return sum(items)\n"),
                    "orders.py": (
                        "from utils import calculate_total\n"
                        "\nresult = calculate_total([1, 2, 3])\n"
                    ),
                },
                1,
                "calculate_total",
                id="function-removed-and-referenced",
            ),
            pytest.param(
                {
                    "models.py": "class UserProfile:\n    pass\n",
                },
                {
                    "models.py": ("class AccountProfile:\n    pass\n"),
                    "views.py": "profile = UserProfile()\n",
                },
                1,
                "UserProfile",
                id="class-removed-and-referenced",
            ),
            pytest.param(
                {
                    "config.py": "MAX_RETRIES = 3\n",
                },
                {
                    "config.py": "RETRY_LIMIT = 3\n",
                    "client.py": ("for i in range(MAX_RETRIES):\n    pass\n"),
                },
                1,
                "MAX_RETRIES",
                id="constant-removed-and-referenced",
            ),
            pytest.param(
                {
                    "utils.py": "def old_func():\n    pass\n",
                },
                {
                    "utils.py": "def new_func():\n    pass\n",
                    "other.py": "x = 42\n",
                },
                0,
                None,
                id="removed-but-not-referenced",
            ),
            pytest.param(
                {
                    "utils.py": (
                        "def calculate_total(items):\n    return sum(items)\n"
                    ),
                },
                {
                    "utils.py": (
                        "def calculate_total(items):\n    return sum(items)\n"
                    ),
                    "orders.py": ("result = calculate_total([1, 2, 3])\n"),
                },
                0,
                None,
                id="name-still-present-no-conflict",
            ),
        ],
    )
    def test_removed_references(
        self,
        base_sources: dict[str, str],
        merged_sources: dict[str, str],
        expected_min: int,
        description_contains: str | None,
    ) -> None:
        """Parametrized test for check_removed_references."""
        conflicts = check_removed_references(
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert len(conflicts) >= expected_min
        if expected_min == 0:
            assert len(conflicts) == 0
        if expected_min >= 1:
            assert all(c.conflict_type == ConflictType.SEMANTIC for c in conflicts)
        if description_contains is not None:
            assert any(description_contains in c.description for c in conflicts)

    def test_empty_sources_no_conflict(self) -> None:
        """Empty sources yield an empty tuple."""
        conflicts = check_removed_references(
            base_sources={},
            merged_sources={},
        )
        assert conflicts == ()

    def test_syntax_error_in_source_skipped(self) -> None:
        """Unparseable files are skipped without raising."""
        base_sources = {
            "bad.py": "def foo(\n",
        }
        merged_sources = {
            "bad.py": "x = 1\n",
            "other.py": "foo()\n",
        }
        conflicts = check_removed_references(
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert isinstance(conflicts, tuple)


# -------------------------------------------------------------------
# check_signature_changes
# -------------------------------------------------------------------


class TestCheckSignatureChanges:
    """Tests for detecting function signature incompatibilities."""

    @pytest.mark.parametrize(
        (
            "base_sources",
            "merged_sources",
            "expected_min",
            "description_contains",
        ),
        [
            pytest.param(
                {
                    "utils.py": ("def process(data, verbose=False):\n    pass\n"),
                },
                {
                    "utils.py": "def process(data):\n    pass\n",
                    "main.py": ("process(data, verbose=True)\n"),
                },
                1,
                "process",
                id="param-removed-callers-break",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data):\n    pass\n"),
                },
                {
                    "utils.py": ("def process(data, mode):\n    pass\n"),
                    "main.py": "process(items)\n",
                },
                1,
                None,
                id="required-param-added-callers-break",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data, verbose=False):\n    pass\n"),
                },
                {
                    "utils.py": "def process(data):\n    pass\n",
                    "main.py": ("process(data, verbose=True)\n"),
                },
                1,
                "verbose",
                id="keyword-arg-removed-detected",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data):\n    pass\n"),
                },
                {
                    "utils.py": ("def process(data, mode='fast'):\n    pass\n"),
                    "main.py": "process(items)\n",
                },
                0,
                None,
                id="default-param-added-no-break",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data, verbose=False):\n    pass\n"),
                },
                {
                    "utils.py": "def process(data):\n    pass\n",
                    "other.py": "x = 42\n",
                },
                0,
                None,
                id="no-callers-no-conflict",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data, *args):\n    pass\n"),
                },
                {
                    "utils.py": ("def process(data, *args):\n    pass\n"),
                    "main.py": "process(1, 2, 3, 4)\n",
                },
                0,
                None,
                id="varargs-accepts-extra-positional",
            ),
            pytest.param(
                {
                    "utils.py": ("def process(data, verbose=False):\n    pass\n"),
                },
                {
                    "utils.py": ("def process(data, verbose=False):\n    pass\n"),
                    "main.py": ("process(data, verbose=True)\n"),
                },
                0,
                None,
                id="signature-unchanged-no-conflict",
            ),
        ],
    )
    def test_signature_changes(
        self,
        base_sources: dict[str, str],
        merged_sources: dict[str, str],
        expected_min: int,
        description_contains: str | None,
    ) -> None:
        """Parametrized test for check_signature_changes."""
        conflicts = check_signature_changes(
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert len(conflicts) >= expected_min
        if expected_min == 0:
            assert len(conflicts) == 0
        if description_contains is not None:
            assert any(description_contains in c.description for c in conflicts)


# -------------------------------------------------------------------
# check_duplicate_definitions
# -------------------------------------------------------------------


class TestCheckDuplicateDefinitions:
    """Tests for detecting duplicate top-level definitions."""

    @pytest.mark.parametrize(
        (
            "merged_sources",
            "expected_min",
            "description_contains",
        ),
        [
            pytest.param(
                {
                    "utils.py": (
                        "def process(data):\n"
                        "    return data.upper()\n"
                        "\n"
                        "def process(data):\n"
                        "    return data.lower()\n"
                    ),
                },
                1,
                "process",
                id="duplicate-function-same-file",
            ),
            pytest.param(
                {
                    "models.py": (
                        "class Widget:\n"
                        "    color = 'red'\n"
                        "\n"
                        "class Widget:\n"
                        "    color = 'blue'\n"
                    ),
                },
                1,
                "Widget",
                id="duplicate-class-same-file",
            ),
            pytest.param(
                {
                    "utils.py": ("def foo():\n    pass\n\ndef bar():\n    pass\n"),
                },
                0,
                None,
                id="no-duplicates-no-conflict",
            ),
            pytest.param(
                {
                    "utils.py": (
                        "class Outer:\n"
                        "    def helper(self):\n"
                        "        pass\n"
                        "\n"
                        "def helper():\n"
                        "    pass\n"
                    ),
                },
                0,
                None,
                id="nested-same-name-not-flagged",
            ),
        ],
    )
    def test_duplicate_definitions(
        self,
        merged_sources: dict[str, str],
        expected_min: int,
        description_contains: str | None,
    ) -> None:
        """Parametrized test for check_duplicate_definitions."""
        conflicts = check_duplicate_definitions(
            merged_sources=merged_sources,
        )
        assert len(conflicts) >= expected_min
        if expected_min == 0:
            assert len(conflicts) == 0
        if expected_min >= 1:
            assert all(c.conflict_type == ConflictType.SEMANTIC for c in conflicts)
        if description_contains is not None:
            assert any(description_contains in c.description for c in conflicts)

    def test_empty_sources(self) -> None:
        """Empty sources yield an empty tuple."""
        conflicts = check_duplicate_definitions(
            merged_sources={},
        )
        assert conflicts == ()

    def test_syntax_error_skipped(self) -> None:
        """Unparseable files are skipped without raising."""
        merged_sources = {"bad.py": "def foo(\n"}
        conflicts = check_duplicate_definitions(
            merged_sources=merged_sources,
        )
        assert isinstance(conflicts, tuple)


# -------------------------------------------------------------------
# check_import_conflicts
# -------------------------------------------------------------------


class TestCheckImportConflicts:
    """Tests for detecting imports of removed exports."""

    @pytest.mark.parametrize(
        (
            "base_sources",
            "merged_sources",
            "expected_min",
            "description_contains",
        ),
        [
            pytest.param(
                {
                    "utils.py": (
                        "def helper():\n    pass\n\ndef process():\n    pass\n"
                    ),
                },
                {
                    "utils.py": ("def process():\n    pass\n"),
                    "main.py": ("from utils import helper\n"),
                },
                1,
                "helper",
                id="import-of-removed-name",
            ),
            pytest.param(
                {
                    "models.py": ("class Config:\n    pass\n"),
                },
                {
                    "models.py": ("class Settings:\n    pass\n"),
                    "app.py": ("from models import Config\n"),
                },
                1,
                "Config",
                id="class-removed-import-breaks",
            ),
            pytest.param(
                {
                    "utils.py": ("def helper():\n    pass\n"),
                },
                {
                    "utils.py": ("def helper():\n    pass\n"),
                    "main.py": ("from utils import helper\n"),
                },
                0,
                None,
                id="import-still-valid",
            ),
            pytest.param(
                {
                    "utils.py": ("def helper():\n    pass\n"),
                },
                {
                    "utils.py": ("def new_helper():\n    pass\n"),
                    "main.py": ("from utils import *\n"),
                },
                0,
                None,
                id="import-star-not-flagged",
            ),
        ],
    )
    def test_import_conflicts(
        self,
        base_sources: dict[str, str],
        merged_sources: dict[str, str],
        expected_min: int,
        description_contains: str | None,
    ) -> None:
        """Parametrized test for check_import_conflicts."""
        conflicts = check_import_conflicts(
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert len(conflicts) >= expected_min
        if expected_min == 0:
            assert len(conflicts) == 0
        if description_contains is not None:
            assert any(description_contains in c.description for c in conflicts)

    def test_empty_sources(self) -> None:
        """Empty sources yield an empty tuple."""
        conflicts = check_import_conflicts(
            base_sources={},
            merged_sources={},
        )
        assert conflicts == ()
