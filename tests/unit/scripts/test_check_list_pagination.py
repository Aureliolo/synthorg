"""Tests for the list_*/query_* pagination pre-push gate."""

import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class WriteSampleFile(Protocol):
    """Callable signature for the ``write_sample`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


class _CheckListPaginationModule(Protocol):
    """Subset of ``scripts/check_list_pagination.py`` exercised by tests."""

    InspectionError: type[Exception]
    _OPT_OUT_MARKER: str
    _REPO_ROOT: Path
    _PERSISTENCE_ROOT: Path
    _BASELINE_PATH: Path

    @staticmethod
    def _load_baseline() -> set[str]: ...
    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[tuple[str, str, str, int]]: ...
    @staticmethod
    def _format_entry(
        rel: str, class_name: str, method_name: str, reason: str
    ) -> str: ...
    @staticmethod
    def cmd_scan_paths(paths: Iterable[str]) -> int: ...
    @staticmethod
    def cmd_scan_all() -> int: ...


def _load_module() -> _CheckListPaginationModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_list_pagination.py"
    spec = importlib.util.spec_from_file_location("check_list_pagination", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_CheckListPaginationModule, module)


_MODULE = _load_module()


@pytest.fixture
def write_sample(tmp_path: Path) -> WriteSampleFile:
    """Return a helper that writes a synthetic Python source file."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


def _scan(path: Path) -> list[tuple[str, str, str, int]]:
    return _MODULE._scan_file(path, "fake/sample.py")


class TestListPaginationGate:
    """Signature-level pagination contract for repository methods."""

    def test_list_with_required_limit_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(self, *, limit: int) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_explicit_numeric_default_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(self, *, limit: int = 100) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_none_default_and_cursor_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(\n"
            "        self,\n"
            "        *,\n"
            "        limit: int | None = None,\n"
            "        cursor: str | None = None,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_none_default_and_offset_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(\n"
            "        self,\n"
            "        *,\n"
            "        limit: int | None = None,\n"
            "        offset: int = 0,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_none_default_no_cursor_fails(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(\n"
            "        self,\n"
            "        *,\n"
            "        limit: int | None = None,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        cls, method, reason, _ = violations[0]
        assert cls == "FooRepository"
        assert method == "list_items"
        assert reason == "nullable-limit-no-cursor"

    def test_list_without_limit_fails(self, write_sample: WriteSampleFile) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        cls, method, reason, _ = violations[0]
        assert cls == "FooRepository"
        assert method == "list_items"
        assert reason == "missing-limit-param"

    def test_query_underscore_with_required_limit_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def query_costs(self, *, limit: int) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_query_underscore_without_limit_fails(
        self, write_sample: WriteSampleFile
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def query_costs(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        assert violations[0][1] == "query_costs"
        assert violations[0][2] == "missing-limit-param"

    def test_bare_query_treated_like_query_underscore(
        self, write_sample: WriteSampleFile
    ) -> None:
        """``query`` (no underscore) is the canonical CRUD name in §14."""
        src = (
            "class FooRepository:\n"
            "    def query(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        assert violations[0][1] == "query"
        assert violations[0][2] == "missing-limit-param"

    def test_async_list_follows_same_rules(self, write_sample: WriteSampleFile) -> None:
        src = (
            "class FooRepository:\n"
            "    async def list_widgets(self) -> tuple[int, ...]:\n"
            "        ...\n"
            "    async def list_gadgets(self, *, limit: int = 50) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        assert violations[0][1] == "list_widgets"
        assert violations[0][2] == "missing-limit-param"

    def test_private_methods_skipped(self, write_sample: WriteSampleFile) -> None:
        """``_list_*`` is a private helper, not a public list endpoint."""
        src = (
            "class FooRepository:\n"
            "    def _list_internal(self) -> tuple[int, ...]:\n"
            "        ...\n"
            "    async def _query_internal(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_non_list_query_methods_not_flagged(
        self, write_sample: WriteSampleFile
    ) -> None:
        """Other names (``get_history``, ``search``, ``list_all``) are out of scope."""
        src = (
            "class FooRepository:\n"
            "    def get_history(self) -> tuple[int, ...]:\n"
            "        ...\n"
            "    def search(self, q: str) -> tuple[int, ...]:\n"
            "        ...\n"
            "    def get_active(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_after_id_cursor_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        """``after_id`` is a recognised keyset-cursor name."""
        src = (
            "class FooRepository:\n"
            "    async def list_pages(\n"
            "        self,\n"
            "        *,\n"
            "        after_id: str | None = None,\n"
            "        limit: int | None = None,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_lint_allow_marker_suppresses_violation(
        self,
        tmp_path: Path,
    ) -> None:
        """A ``# lint-allow: list-pagination -- <reason>`` marker suppresses."""
        src = (
            "class FooRepository:\n"
            "    def list_items(self) -> tuple[int, ...]:  "
            "# lint-allow: list-pagination -- legacy fixed-set\n"
            "        ...\n"
        )
        path = tmp_path / "sample.py"
        path.write_text(src, encoding="utf-8")
        violations = _scan(path)
        assert violations == []

    def test_baseline_entry_suppresses_violation(
        self,
        write_sample: WriteSampleFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entries in the baseline are not re-reported by ``cmd_scan_paths``."""
        src = (
            "class FooRepository:\n"
            "    def list_items(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        path = write_sample(src, name="legacy_repo.py")
        rel = "legacy_repo.py"
        baselined = _MODULE._format_entry(
            rel, "FooRepository", "list_items", "missing-limit-param"
        )
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", path.parent)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", path.parent)
        monkeypatch.setattr(_MODULE, "_load_baseline", lambda: {baselined})
        rc = _MODULE.cmd_scan_paths([str(path)])
        assert rc == 0

    def test_new_offender_not_in_baseline_fails(
        self,
        write_sample: WriteSampleFile,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A new violation absent from the baseline trips the gate."""
        src = (
            "class NewRepository:\n"
            "    def list_widgets(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        path = write_sample(src, name="new_repo.py")
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", path.parent)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", path.parent)
        monkeypatch.setattr(_MODULE, "_load_baseline", set)
        rc = _MODULE.cmd_scan_paths([str(path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "NewRepository.list_widgets" in out
        assert "missing-limit-param" in out

    def test_unparseable_file_raises(self, write_sample: WriteSampleFile) -> None:
        path = write_sample("def broken(:\n")
        with pytest.raises(_MODULE.InspectionError):
            _scan(path)
