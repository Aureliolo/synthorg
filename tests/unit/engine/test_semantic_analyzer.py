"""Unit tests for the semantic analyzer classes.

Tests filter_files, AstSemanticAnalyzer, and CompositeSemanticAnalyzer
with mocked file I/O to verify orchestration and result aggregation.
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.engine.workspace.config import SemanticAnalysisConfig
from synthorg.engine.workspace.enums import ConflictType

# Analyzer no longer reads from disk -- merged_sources are passed directly.
from synthorg.engine.workspace.models import MergeConflict, Workspace
from synthorg.engine.workspace.semantic_analyzer import (
    AstSemanticAnalyzer,
    CompositeSemanticAnalyzer,
    SemanticAnalyzer,
    filter_files,
)

pytestmark = pytest.mark.unit


def _make_workspace(
    workspace_id: str = "ws-1",
    base_branch: str = "main",
) -> Workspace:
    """Create a test workspace."""
    from datetime import UTC, datetime

    return Workspace(
        workspace_id=workspace_id,
        task_id="task-1",
        agent_id="agent-1",
        branch_name=f"ws/{workspace_id}",
        worktree_path="/tmp/ws",  # noqa: S108
        base_branch=base_branch,
        created_at=datetime.now(tz=UTC),
    )


def _make_conflict(
    file_path: str = "utils.py",
    description: str = "test conflict",
) -> MergeConflict:
    """Create a test semantic conflict."""
    return MergeConflict(
        file_path=file_path,
        conflict_type=ConflictType.SEMANTIC,
        description=description,
    )


# ---------------------------------------------------------------------------
# filter_files
# ---------------------------------------------------------------------------


class TestFilterFiles:
    """Tests for the shared file-filtering utility."""

    def test_empty_input(self) -> None:
        config = SemanticAnalysisConfig()
        assert filter_files((), config) == []

    def test_filters_by_extension(self) -> None:
        config = SemanticAnalysisConfig(file_extensions=(".py",))
        result = filter_files(
            ("app.py", "readme.md", "config.yaml", "utils.py"),
            config,
        )
        assert result == ["app.py", "utils.py"]

    def test_multiple_extensions(self) -> None:
        config = SemanticAnalysisConfig(
            file_extensions=(".py", ".pyi"),
        )
        result = filter_files(
            ("app.py", "stubs.pyi", "readme.md"),
            config,
        )
        assert result == ["app.py", "stubs.pyi"]

    def test_max_files_limit(self) -> None:
        config = SemanticAnalysisConfig(max_files=3)
        files = tuple(f"file{i}.py" for i in range(10))
        result = filter_files(files, config)
        assert len(result) == 3
        assert result == ["file0.py", "file1.py", "file2.py"]

    def test_no_matching_extensions(self) -> None:
        config = SemanticAnalysisConfig(file_extensions=(".py",))
        result = filter_files(("readme.md", "config.yaml"), config)
        assert result == []

    def test_extension_filter_applied_before_max_files(self) -> None:
        """Extension filtering narrows before max_files truncates."""
        config = SemanticAnalysisConfig(
            file_extensions=(".py",),
            max_files=2,
        )
        result = filter_files(
            ("a.md", "b.py", "c.md", "d.py", "e.py"),
            config,
        )
        assert result == ["b.py", "d.py"]


# ---------------------------------------------------------------------------
# AstSemanticAnalyzer
# ---------------------------------------------------------------------------


class TestAstSemanticAnalyzer:
    """Tests for the AST-based semantic analyzer."""

    async def test_protocol_compliance(self) -> None:
        analyzer = AstSemanticAnalyzer(config=SemanticAnalysisConfig())
        assert isinstance(analyzer, SemanticAnalyzer)

    async def test_no_changed_files_returns_empty(self) -> None:
        analyzer = AstSemanticAnalyzer(config=SemanticAnalysisConfig())
        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=(),
            base_sources={},
            merged_sources={},
        )
        assert result == ()

    async def test_non_python_files_skipped(self) -> None:
        analyzer = AstSemanticAnalyzer(
            config=SemanticAnalysisConfig(file_extensions=(".py",)),
        )
        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("readme.md", "config.yaml"),
            base_sources={},
            merged_sources={"readme.md": "# Hi\n"},
        )
        assert result == ()

    async def test_max_files_limit_respected(self) -> None:
        config = SemanticAnalysisConfig(max_files=2)
        analyzer = AstSemanticAnalyzer(config=config)
        files = tuple(f"file{i}.py" for i in range(10))
        all_merged = {f"file{i}.py": "x = 1\n" for i in range(10)}

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=files,
            base_sources={},
            merged_sources=all_merged,
        )
        assert isinstance(result, tuple)

    async def test_detects_semantic_conflict(self) -> None:
        """End-to-end: function renamed in one file, called by old name."""
        analyzer = AstSemanticAnalyzer(config=SemanticAnalysisConfig())

        base_sources = {
            "utils.py": "def calculate_total(items):\n    return sum(items)\n",
        }
        merged_sources = {
            "utils.py": "def compute_total(items):\n    return sum(items)\n",
            "orders.py": (
                "from utils import calculate_total\n\n"
                "result = calculate_total([1, 2, 3])\n"
            ),
        }

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("utils.py", "orders.py"),
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert len(result) >= 1
        assert all(c.conflict_type == ConflictType.SEMANTIC for c in result)

    async def test_no_conflict_when_clean_merge(self) -> None:
        analyzer = AstSemanticAnalyzer(config=SemanticAnalysisConfig())

        base_sources = {
            "utils.py": "def process(data):\n    pass\n",
        }
        merged_sources = {
            "utils.py": "def process(data):\n    pass\n\ndef extra():\n    pass\n",
            "main.py": "from utils import process\n\nprocess(42)\n",
        }

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("utils.py", "main.py"),
            base_sources=base_sources,
            merged_sources=merged_sources,
        )
        assert len(result) == 0

    async def test_empty_merged_sources_returns_empty(self) -> None:
        analyzer = AstSemanticAnalyzer(config=SemanticAnalysisConfig())

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("missing.py",),
            base_sources={},
            merged_sources={},
        )
        assert result == ()


# ---------------------------------------------------------------------------
# CompositeSemanticAnalyzer
# ---------------------------------------------------------------------------


class TestCompositeSemanticAnalyzer:
    """Tests for the composite analyzer that chains multiple analyzers."""

    async def test_empty_analyzers_returns_empty(self) -> None:
        composite = CompositeSemanticAnalyzer(analyzers=())
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("foo.py",),
            base_sources={},
            merged_sources={},
        )
        assert result == ()

    async def test_aggregates_results_from_multiple_analyzers(self) -> None:
        conflict_a = _make_conflict("a.py", "conflict from A")
        conflict_b = _make_conflict("b.py", "conflict from B")

        analyzer_a = AsyncMock(spec=SemanticAnalyzer)
        analyzer_a.analyze.return_value = (conflict_a,)

        analyzer_b = AsyncMock(spec=SemanticAnalyzer)
        analyzer_b.analyze.return_value = (conflict_b,)

        composite = CompositeSemanticAnalyzer(
            analyzers=(analyzer_a, analyzer_b),
        )
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("a.py", "b.py"),
            base_sources={},
            merged_sources={},
        )
        assert len(result) == 2
        descriptions = {c.description for c in result}
        assert "conflict from A" in descriptions
        assert "conflict from B" in descriptions

    async def test_deduplicates_by_file_path_and_description(self) -> None:
        conflict = _make_conflict("a.py", "same issue")

        analyzer_a = AsyncMock(spec=SemanticAnalyzer)
        analyzer_a.analyze.return_value = (conflict,)

        analyzer_b = AsyncMock(spec=SemanticAnalyzer)
        analyzer_b.analyze.return_value = (conflict,)

        composite = CompositeSemanticAnalyzer(
            analyzers=(analyzer_a, analyzer_b),
        )
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("a.py",),
            base_sources={},
            merged_sources={},
        )
        assert len(result) == 1

    async def test_single_analyzer_failure_does_not_block_others(self) -> None:
        conflict = _make_conflict("ok.py", "found issue")

        failing = AsyncMock(spec=SemanticAnalyzer)
        failing.analyze.side_effect = Exception("analyzer failed")

        working = AsyncMock(spec=SemanticAnalyzer)
        working.analyze.return_value = (conflict,)

        composite = CompositeSemanticAnalyzer(
            analyzers=(failing, working),
        )
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("ok.py",),
            base_sources={},
            merged_sources={},
        )
        assert len(result) == 1

    async def test_protocol_compliance(self) -> None:
        composite = CompositeSemanticAnalyzer(analyzers=())
        assert isinstance(composite, SemanticAnalyzer)

    async def test_runs_analyzers_concurrently(self) -> None:
        """Verify analyzers execute in parallel, not sequentially.

        Analyzer B blocks until analyzer A signals. If run sequentially
        (A then B), this works. But if run as (B then A), B would
        deadlock waiting for A. We put B first to prove true concurrency.
        The pytest global timeout (30s) acts as the safety net if
        execution is truly sequential.
        """
        import asyncio

        gate = asyncio.Event()

        async def _analyze_a(**kwargs: object) -> tuple[MergeConflict, ...]:
            gate.set()
            return ()

        async def _analyze_b(**kwargs: object) -> tuple[MergeConflict, ...]:
            # If sequential and B runs first, this deadlocks
            await gate.wait()
            return (_make_conflict("b.py", "from B"),)

        analyzer_a = AsyncMock(spec=SemanticAnalyzer)
        analyzer_a.analyze.side_effect = _analyze_a

        analyzer_b = AsyncMock(spec=SemanticAnalyzer)
        analyzer_b.analyze.side_effect = _analyze_b

        # B is first -- sequential execution would deadlock
        composite = CompositeSemanticAnalyzer(
            analyzers=(analyzer_b, analyzer_a),
        )
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("b.py",),
            base_sources={},
            merged_sources={},
        )
        assert len(result) == 1

    async def test_all_analyzers_fail_returns_empty(self) -> None:
        failing_a = AsyncMock(spec=SemanticAnalyzer)
        failing_a.analyze.side_effect = RuntimeError("A failed")

        failing_b = AsyncMock(spec=SemanticAnalyzer)
        failing_b.analyze.side_effect = ValueError("B failed")

        composite = CompositeSemanticAnalyzer(
            analyzers=(failing_a, failing_b),
        )
        result = await composite.analyze(
            workspace=_make_workspace(),
            changed_files=("x.py",),
            base_sources={},
            merged_sources={},
        )
        assert result == ()

    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError must not be swallowed by the Exception handler."""
        import asyncio

        async def _blocking(**kwargs: object) -> tuple[MergeConflict, ...]:
            await asyncio.Event().wait()
            return ()  # pragma: no cover

        analyzer = AsyncMock(spec=SemanticAnalyzer)
        analyzer.analyze.side_effect = _blocking

        composite = CompositeSemanticAnalyzer(analyzers=(analyzer,))
        task = asyncio.create_task(
            composite.analyze(
                workspace=_make_workspace(),
                changed_files=("a.py",),
                base_sources={},
                merged_sources={},
            ),
        )
        await asyncio.sleep(0)  # let the task start
        task.cancel()
        with pytest.raises((asyncio.CancelledError, BaseExceptionGroup)):
            await task


# ---------------------------------------------------------------------------
# SemanticAnalysisConfig defaults
# ---------------------------------------------------------------------------


class TestSemanticAnalysisConfig:
    """Tests for config fields relevant to semantic analysis."""

    def test_git_concurrency_default(self) -> None:
        config = SemanticAnalysisConfig()
        assert config.git_concurrency == 10

    def test_git_concurrency_bounds(self) -> None:
        with pytest.raises(
            ValidationError,
            match="git_concurrency",
        ):
            SemanticAnalysisConfig(git_concurrency=0)
        with pytest.raises(
            ValidationError,
            match="git_concurrency",
        ):
            SemanticAnalysisConfig(git_concurrency=51)
