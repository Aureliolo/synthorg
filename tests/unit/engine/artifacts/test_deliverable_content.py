"""Unit tests for reading a task's declared artifacts for review.

The reviewer's verdict decides delivery, so what it can and cannot see is
the contract: every declared path is accounted for (present, absent or
truncated), and a bound is announced rather than silently applied.
"""

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.deliverable_content import (
    read_declared_artifacts,
    workspace_deliverable_reader,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PER_FILE = 20000
_TOTAL = 60000


def _expected(*paths: str) -> tuple[ExpectedArtifact, ...]:
    """Declare *paths* as expected code artifacts.

    Returns:
        One :class:`ExpectedArtifact` per path.
    """
    return tuple(
        ExpectedArtifact(type=ArtifactType.CODE, path=NotBlankStr(path))
        for path in paths
    )


def _write(root: Path, relpath: str, body: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _read(
    expected: Sequence[ExpectedArtifact],
    workspace: Path,
    *,
    per_file: int = _PER_FILE,
    total: int = _TOTAL,
) -> str | None:
    """Read *expected* under *workspace* with the given bounds.

    Returns:
        The assembled deliverable text.
    """
    return read_declared_artifacts(
        expected,
        workspace=workspace,
        max_bytes_per_file=per_file,
        max_total_bytes=total,
    )


class TestReadDeclaredArtifacts:
    def test_content_is_labelled_by_path(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/game.py", "def rotate(): ...")

        content = _read(_expected("src/game.py"), tmp_path)

        assert content is not None
        assert "--- src/game.py ---" in content
        assert "def rotate(): ..." in content

    def test_every_declared_path_is_present(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "first")
        _write(tmp_path, "b.py", "second")

        content = _read(_expected("a.py", "b.py"), tmp_path)

        assert content is not None
        assert "first" in content
        assert "second" in content

    def test_an_absent_file_is_reported_not_hidden(self, tmp_path: Path) -> None:
        """A reviewer must know a promised file is missing.

        Hiding it would leave the reviewer approving a deliverable it does
        not know is incomplete.
        """
        _write(tmp_path, "a.py", "first")

        content = _read(_expected("a.py", "missing.py"), tmp_path)

        assert content is not None
        assert "missing.py" in content
        assert "(not produced)" in content

    def test_a_directory_is_named_as_one(self, tmp_path: Path) -> None:
        (tmp_path / "dist").mkdir()

        content = _read(_expected("dist"), tmp_path)

        assert content is not None
        assert "(directory)" in content

    def test_a_path_escaping_the_workspace_reads_as_absent(
        self, tmp_path: Path
    ) -> None:
        """A file the run could not have written is not its output."""
        outside = tmp_path.parent / "outside.py"
        outside.write_text("not ours", encoding="utf-8")
        workspace = tmp_path / "project"
        workspace.mkdir()

        content = _read(_expected(f"../{outside.name}"), workspace)

        assert content is not None
        assert "not ours" not in content
        assert "(not produced)" in content

    def test_per_file_truncation_is_announced(self, tmp_path: Path) -> None:
        _write(tmp_path, "big.py", "x" * 500)

        content = _read(_expected("big.py"), tmp_path, per_file=100)

        assert content is not None
        assert "... (truncated)" in content
        assert content.count("x") == 100

    def test_the_total_bound_names_what_was_dropped(self, tmp_path: Path) -> None:
        """Silent truncation reads as "covered everything" when it did not."""
        for name in ("a.py", "b.py", "c.py"):
            _write(tmp_path, name, "y" * 100)

        content = _read(_expected("a.py", "b.py", "c.py"), tmp_path, total=100)

        assert content is not None
        assert "further artifact(s) omitted" in content

    def test_nothing_declared_reads_as_nothing(self, tmp_path: Path) -> None:
        assert _read((), tmp_path) is None

    def test_binary_content_does_not_raise(self, tmp_path: Path) -> None:
        """A generated binary is still a declared artifact."""
        path = tmp_path / "asset.bin"
        path.write_bytes(b"\xff\xfe\x00binary")

        content = _read(_expected("asset.bin"), tmp_path)

        assert content is not None
        assert "asset.bin" in content


class TestWorkspaceDeliverableReader:
    async def test_reads_the_projects_own_workspace(self, tmp_path: Path) -> None:
        _write(tmp_path, "projects/proj-1/src/game.py", "def rotate(): ...")
        reader = workspace_deliverable_reader(tmp_path)

        content = await reader("proj-1", _expected("src/game.py"))

        assert content is not None
        assert "def rotate(): ..." in content

    async def test_bounds_are_read_live_per_review(self, tmp_path: Path) -> None:
        """An operator retune arms the next review, not the next boot."""
        _write(tmp_path, "projects/proj-1/big.py", "x" * 5000)
        resolver = mock_of[ConfigResolverProtocol](
            get_int=AsyncMock(return_value=50),
        )
        reader = workspace_deliverable_reader(tmp_path, config_resolver=resolver)

        content = await reader("proj-1", _expected("big.py"))

        assert content is not None
        assert content.count("x") == 50
        assert "... (truncated)" in content
