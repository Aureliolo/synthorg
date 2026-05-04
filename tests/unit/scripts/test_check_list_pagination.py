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
    _BASELINE_HEADER: str

    @staticmethod
    def _load_baseline() -> set[str]: ...
    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[tuple[str, str, str, int]]: ...
    @staticmethod
    def _scan(path: Path, baseline: set[str]) -> tuple[list[str], set[str]]: ...
    @staticmethod
    def _format_entry(
        rel: str, class_name: str, method_name: str, reason: str
    ) -> str: ...
    @staticmethod
    def cmd_scan_paths(paths: Iterable[str]) -> int: ...
    @staticmethod
    def cmd_scan_all() -> int: ...
    @staticmethod
    def cmd_update() -> int: ...


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
        """``query`` (no underscore) is the canonical CRUD name."""
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

    @pytest.mark.parametrize(
        "cursor_name", ["cursor", "offset", "after_id", "before_id"]
    )
    def test_list_with_any_cursor_name_passes(
        self, write_sample: WriteSampleFile, cursor_name: str
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    async def list_pages(\n"
            "        self,\n"
            "        *,\n"
            f"        {cursor_name}: str | None = None,\n"
            "        limit: int | None = None,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    @pytest.mark.parametrize(
        "annotation",
        ["Sequence[str]", "list[str]", "tuple[str, ...]", "Iterable[str]"],
    )
    def test_list_with_required_sequence_filter_passes(
        self, write_sample: WriteSampleFile, annotation: str
    ) -> None:
        """A required Sequence-typed filter bounds the result by input cardinality."""
        src = (
            "class FooRepository:\n"
            "    async def list_by_ids(\n"
            "        self,\n"
            "        *,\n"
            f"        ids: {annotation},\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_list_with_optional_sequence_filter_fails(
        self, write_sample: WriteSampleFile
    ) -> None:
        """A defaulted Sequence (caller can omit) does NOT bound the result."""
        src = (
            "class FooRepository:\n"
            "    async def list_by_ids(\n"
            "        self,\n"
            "        *,\n"
            "        ids: list[str] | None = None,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        violations = _scan(write_sample(src))
        assert len(violations) == 1
        assert violations[0][2] == "missing-limit-param"

    def test_non_literal_default_expression_passes(
        self, write_sample: WriteSampleFile
    ) -> None:
        """``limit: int = SOME_CONST`` slips through by design (loophole)."""
        src = (
            "class FooRepository:\n"
            "    def list_items(\n"
            "        self,\n"
            "        *,\n"
            "        limit: int = PageSize.DEFAULT,\n"
            "    ) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        assert _scan(write_sample(src)) == []

    def test_lint_allow_marker_in_comment_suppresses(
        self,
        tmp_path: Path,
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(self) -> tuple[int, ...]:  "
            "# lint-allow: list-pagination -- legacy fixed-set\n"
            "        ...\n"
        )
        path = tmp_path / "sample.py"
        path.write_text(src, encoding="utf-8")
        assert _scan(path) == []

    def test_lint_allow_marker_in_string_default_does_not_suppress(
        self,
        tmp_path: Path,
    ) -> None:
        """The marker only counts when in the actual comment, not in a string."""
        src = (
            "class FooRepository:\n"
            "    def list_items(self, msg: str = "
            "'lint-allow: list-pagination -- fake'):\n"
            "        ...\n"
        )
        path = tmp_path / "sample.py"
        path.write_text(src, encoding="utf-8")
        violations = _scan(path)
        assert len(violations) == 1
        assert violations[0][2] == "missing-limit-param"

    def test_baseline_entry_suppresses_violation(
        self,
        write_sample: WriteSampleFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src = (
            "class FooRepository:\n"
            "    def list_items(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        path = write_sample(src, name="legacy_repo.py")
        baselined = _MODULE._format_entry(
            "legacy_repo.py", "FooRepository", "list_items", "missing-limit-param"
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

    def test_cmd_scan_paths_skips_paths_outside_persistence_root(
        self,
        write_sample: WriteSampleFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pre-push must never crash on files outside the persistence tree."""
        src = (
            "class NewRepository:\n"
            "    def list_widgets(self) -> tuple[int, ...]:\n"
            "        ...\n"
        )
        path = write_sample(src, name="api_handler.py")
        outside_root = path.parent / "other_root"
        outside_root.mkdir()
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", path.parent)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", outside_root)
        monkeypatch.setattr(_MODULE, "_load_baseline", set)
        rc = _MODULE.cmd_scan_paths([str(path)])
        assert rc == 0

    def test_cmd_scan_paths_exit_2_on_parse_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A syntax-broken file inside persistence root fails loudly (exit 2)."""
        broken = tmp_path / "broken_repo.py"
        broken.write_text("def broken(:\n", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_load_baseline", set)
        rc = _MODULE.cmd_scan_paths([str(broken)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "broken_repo.py" in err
        assert "SyntaxError" in err

    def test_cmd_scan_all_reports_baseline_stale_for_shrinkage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A baseline entry that no longer matches surfaces as ``baseline-stale``."""
        # Persistence dir contains one offender that IS in the baseline.
        compliant_repo = tmp_path / "compliant_repo.py"
        compliant_repo.write_text(
            "class FooRepository:\n"
            "    def list_items(self, *, limit: int = 100) -> tuple[int, ...]:\n"
            "        ...\n",
            encoding="utf-8",
        )
        # Baseline has an entry for a DIFFERENT method that no longer exists.
        stale_entry = _MODULE._format_entry(
            "compliant_repo.py",
            "FooRepository",
            "list_orphans",
            "missing-limit-param",
        )
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_load_baseline", lambda: {stale_entry})
        rc = _MODULE.cmd_scan_all()
        assert rc == 1
        out = capsys.readouterr().out
        assert f"baseline-stale: {stale_entry}" in out

    def test_cmd_scan_all_exit_2_on_parse_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A parse failure during full scan exits 2, not 1."""
        broken = tmp_path / "broken_repo.py"
        broken.write_text("def broken(:\n", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_load_baseline", set)
        rc = _MODULE.cmd_scan_all()
        assert rc == 2

    def test_cmd_update_writes_sorted_baseline_with_header(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--update`` regenerates a sorted, header-prefixed baseline."""
        # Two offenders in alphabetical-reverse order across two files.
        (tmp_path / "z_repo.py").write_text(
            "class ZRepository:\n"
            "    def list_z(self) -> tuple[int, ...]:\n"
            "        ...\n",
            encoding="utf-8",
        )
        (tmp_path / "a_repo.py").write_text(
            "class ARepository:\n"
            "    def list_a(self) -> tuple[int, ...]:\n"
            "        ...\n",
            encoding="utf-8",
        )
        baseline_target = tmp_path / "baseline.txt"
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_PERSISTENCE_ROOT", tmp_path)
        monkeypatch.setattr(_MODULE, "_BASELINE_PATH", baseline_target)
        rc = _MODULE.cmd_update()
        assert rc == 0
        body = baseline_target.read_text(encoding="utf-8")
        assert body.startswith(_MODULE._BASELINE_HEADER)
        entries = [
            line for line in body.splitlines() if line and not line.startswith("#")
        ]
        assert entries == sorted(entries)
        assert any("ARepository.list_a" in e for e in entries)
        assert any("ZRepository.list_z" in e for e in entries)

    def test_load_baseline_returns_empty_when_file_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Init case: baseline file missing → empty allowlist, no crash."""
        missing = tmp_path / "does_not_exist.txt"
        monkeypatch.setattr(_MODULE, "_BASELINE_PATH", missing)
        assert _MODULE._load_baseline() == set()

    def test_load_baseline_rejects_malformed_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Malformed line raises ValueError with diagnostic on stderr."""
        baseline = tmp_path / "baseline.txt"
        baseline.write_text(
            "# header comment\n"
            "src/synthorg/persistence/x.py:Foo.bar:missing-limit-param\n"
            "this-is-not-a-valid-entry\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_MODULE, "_BASELINE_PATH", baseline)
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        with pytest.raises(ValueError, match="failed validation"):
            _MODULE._load_baseline()
        err = capsys.readouterr().err
        assert "malformed entry" in err
        assert "this-is-not-a-valid-entry" in err

    def test_load_baseline_rejects_duplicate_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        baseline = tmp_path / "baseline.txt"
        entry = "src/synthorg/persistence/x.py:Foo.bar:missing-limit-param"
        baseline.write_text(f"{entry}\n{entry}\n", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "_BASELINE_PATH", baseline)
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        with pytest.raises(ValueError, match="failed validation"):
            _MODULE._load_baseline()
        err = capsys.readouterr().err
        assert "duplicate entry" in err

    def test_load_baseline_value_error_includes_first_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exception message embeds the first error for pipeline visibility."""
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("garbage-line\n", encoding="utf-8")
        monkeypatch.setattr(_MODULE, "_BASELINE_PATH", baseline)
        monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
        with pytest.raises(ValueError, match=r"first: .*malformed entry"):
            _MODULE._load_baseline()

    def test_unparseable_file_raises_inspection_error(
        self, write_sample: WriteSampleFile
    ) -> None:
        path = write_sample("def broken(:\n")
        with pytest.raises(_MODULE.InspectionError):
            _scan(path)
