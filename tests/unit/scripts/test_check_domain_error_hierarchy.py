"""Tests for the domain-error-hierarchy AST gate."""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class WriteFile(Protocol):
    """Callable signature for the ``write_file`` fixture."""

    def __call__(self, rel: str, content: str) -> Path: ...


class _GateModule(Protocol):
    """Subset of ``scripts/check_domain_error_hierarchy.py`` the tests exercise."""

    FORBIDDEN_BASES: frozenset[str]
    SUPPRESSION_MARKER: str

    @staticmethod
    def _line_has_trailing_marker(line: str) -> bool: ...
    @staticmethod
    def _module_dotted_for_rel(rel: str) -> str: ...
    @staticmethod
    def _scan_tree(
        project_root: Path,
        scan_root: Path,
        baseline: set[str] | None = None,
    ) -> list[str]: ...
    @staticmethod
    def _format_baseline_entry(rel: str, lineno: int, class_name: str) -> str: ...
    @staticmethod
    def _load_baseline(baseline_path: Path) -> set[str]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_module() -> _GateModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_domain_error_hierarchy.py"
    spec = importlib.util.spec_from_file_location(
        "check_domain_error_hierarchy",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_project(
    tmp_path: Path,
    files: dict[str, str],
) -> tuple[Path, Path]:
    """Materialise a synthetic ``src/synthorg/`` tree under *tmp_path*.

    Always seeds ``synthorg/core/domain_errors.py`` with a minimal
    ``DomainError`` (and the standard intermediates the migration uses
    as bases) so cross-module resolution has a real target to follow.
    """
    project_root = tmp_path
    src_root = project_root / "src" / "synthorg"
    src_root.mkdir(parents=True)
    (src_root / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "core").mkdir()
    (src_root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "core" / "domain_errors.py").write_text(
        "class DomainError(Exception):  "
        "# lint-allow: domain-error-hierarchy -- root of the hierarchy\n"
        "    pass\n"
        "\n"
        "class NotFoundError(DomainError):\n"
        "    pass\n"
        "\n"
        "class ConflictError(DomainError):\n"
        "    pass\n"
        "\n"
        "class ValidationError(DomainError):\n"
        "    pass\n",
        encoding="utf-8",
    )
    for rel, content in files.items():
        target = project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # touch __init__.py up the tree so the module path resolves.
        for parent in target.parents:
            if parent == project_root:
                break
            init = parent / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
    return project_root, src_root


@pytest.fixture
def write_file(tmp_path: Path) -> WriteFile:
    """Return a helper that writes a file under *tmp_path*."""

    def _write(rel: str, content: str) -> Path:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


# ── module-path resolver ─────────────────────────────────────────


def test_module_dotted_for_src_synthorg() -> None:
    assert (
        _MODULE._module_dotted_for_rel("src/synthorg/foo/bar.py") == "synthorg.foo.bar"
    )


def test_module_dotted_for_init() -> None:
    assert (
        _MODULE._module_dotted_for_rel("src/synthorg/foo/__init__.py") == "synthorg.foo"
    )


# ── suppression marker ───────────────────────────────────────────


def test_suppression_marker_present() -> None:
    line = (
        "class Foo(Exception):  # lint-allow: domain-error-hierarchy "
        "-- TSA RFC 3161 client carve-out"
    )
    assert _MODULE._line_has_trailing_marker(line) is True


def test_suppression_marker_requires_justification() -> None:
    line = "class Foo(Exception):  # lint-allow: domain-error-hierarchy --"
    assert _MODULE._line_has_trailing_marker(line) is False


def test_suppression_marker_requires_double_dash() -> None:
    line = "class Foo(Exception):  # lint-allow: domain-error-hierarchy"
    assert _MODULE._line_has_trailing_marker(line) is False


def test_suppression_marker_other_marker_ignored() -> None:
    line = "class Foo(Exception):  # lint-allow: persistence-boundary -- ok"
    assert _MODULE._line_has_trailing_marker(line) is False


# ── positive cases (must NOT flag) ───────────────────────────────


def test_direct_domain_error_subclass_ok(tmp_path: Path) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "\n"
                "class FooError(DomainError):\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert issues == []


def test_intermediate_subclass_ok(tmp_path: Path) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "from synthorg.core.domain_errors import NotFoundError\n"
                "\n"
                "class FooNotFoundError(NotFoundError):\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert issues == []


def test_deeper_mro_ok(tmp_path: Path) -> None:
    """Cross-file transitive: Foo -> Bar -> DomainError must not flag."""
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "\n"
                "class BarError(DomainError):\n"
                "    pass\n"
            ),
            "src/synthorg/foo/sub/errors.py": (
                "from synthorg.foo.errors import BarError\n"
                "\n"
                "class FooError(BarError):\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert issues == []


def test_per_line_marker_ok(tmp_path: Path) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/observability/audit_chain/tsa_client.py": (
                "class TsaError(Exception):  "
                "# lint-allow: domain-error-hierarchy -- RFC 3161 internals\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert issues == []


def test_module_import_alias_ok(tmp_path: Path) -> None:
    """``import x.y as z`` followed by ``class Foo(z.DomainError)``."""
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "import synthorg.core.domain_errors as dom\n"
                "\n"
                "class FooError(dom.DomainError):\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert issues == []


def test_baseline_suppresses(tmp_path: Path) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": ("class FooError(Exception):\n    pass\n"),
        },
    )
    baseline = {"src/synthorg/foo/errors.py:1:FooError"}
    issues = _MODULE._scan_tree(
        project_root,
        project_root / "src" / "synthorg",
        baseline=baseline,
    )
    assert issues == []


# ── negative cases (must flag) ───────────────────────────────────


@pytest.mark.parametrize(
    "base",
    [
        "Exception",
        "RuntimeError",
        "LookupError",
        "PermissionError",
        "ValueError",
    ],
)
def test_direct_stdlib_base_flagged(tmp_path: Path, base: str) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": f"class FooError({base}):\n    pass\n",
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert len(issues) == 1
    assert "FooError" in issues[0]
    assert "src/synthorg/foo/errors.py" in issues[0]


def test_only_root_of_bad_chain_is_flagged(tmp_path: Path) -> None:
    """Only the class whose DIRECT base is a forbidden stdlib name flags.

    `class BarError(Exception)` is flagged. `class FooError(BarError)`
    inherits from BarError -- not a forbidden stdlib base, so FooError
    itself does NOT flag. Migrating BarError to DomainError fixes the
    whole chain in one edit; subclasses don't double-count.
    """
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "class BarError(Exception):\n"
                "    pass\n"
                "\n"
                "class FooError(BarError):\n"
                "    pass\n"
            ),
        },
    )
    issues = _MODULE._scan_tree(project_root, project_root / "src" / "synthorg")
    assert len(issues) == 1
    assert "BarError" in issues[0]


def test_baseline_drift_warns(tmp_path: Path) -> None:
    """Baseline lists a class that no longer exists -> gate flags drift."""
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "\n"
                "class FooError(DomainError):\n"
                "    pass\n"
            ),
        },
    )
    baseline = {"src/synthorg/foo/errors.py:1:GhostError"}
    issues = _MODULE._scan_tree(
        project_root,
        project_root / "src" / "synthorg",
        baseline=baseline,
    )
    assert len(issues) == 1
    assert "GhostError" in issues[0]
    assert "stale" in issues[0].lower() or "drift" in issues[0].lower()


# ── baseline file format ─────────────────────────────────────────


def test_format_baseline_entry() -> None:
    entry = _MODULE._format_baseline_entry(
        "src/synthorg/foo/errors.py",
        42,
        "FooError",
    )
    assert entry == "src/synthorg/foo/errors.py:42:FooError"


def test_load_baseline_skips_comments_and_blanks(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(
        "# header line\n"
        "\n"
        "src/synthorg/foo.py:1:FooError\n"
        "src/synthorg/bar.py:2:BarError\n",
        encoding="utf-8",
    )
    entries = _MODULE._load_baseline(baseline_path)
    assert entries == {
        "src/synthorg/foo.py:1:FooError",
        "src/synthorg/bar.py:2:BarError",
    }


def test_load_baseline_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _MODULE._load_baseline(tmp_path / "absent.txt") == set()


def test_load_baseline_rejects_malformed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text("not-a-valid-entry\n", encoding="utf-8")
    with pytest.raises(ValueError, match="validation"):
        _MODULE._load_baseline(baseline_path)
    captured = capsys.readouterr()
    assert "malformed entry" in captured.err


def test_load_baseline_rejects_duplicates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(
        "src/synthorg/foo.py:1:FooError\nsrc/synthorg/foo.py:1:FooError\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="validation"):
        _MODULE._load_baseline(baseline_path)
    captured = capsys.readouterr()
    assert "duplicate entry" in captured.err


# ── CLI ─────────────────────────────────────────────────────────


def test_no_baseline_flag_reports_all(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": ("class FooError(Exception):\n    pass\n"),
            "scripts/domain_error_hierarchy_baseline.txt": (
                "# baseline\nsrc/synthorg/foo/errors.py:1:FooError\n"
            ),
        },
    )
    monkeypatch.chdir(project_root)
    rc = _MODULE.main(
        [
            "--repo-root",
            str(project_root),
            "--no-baseline",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "FooError" in captured.out


def test_clean_tree_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/foo/errors.py": (
                "from synthorg.core.domain_errors import DomainError\n"
                "\n"
                "class FooError(DomainError):\n"
                "    pass\n"
            ),
        },
    )
    monkeypatch.chdir(project_root)
    rc = _MODULE.main(["--repo-root", str(project_root), "--no-baseline"])
    assert rc == 0


def test_update_baseline_writes_sorted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _make_project(
        tmp_path,
        {
            "src/synthorg/zeta/errors.py": ("class ZetaError(Exception):\n    pass\n"),
            "src/synthorg/alpha/errors.py": (
                "class AlphaError(Exception):\n    pass\n"
            ),
        },
    )
    baseline_path = project_root / "scripts" / "domain_error_hierarchy_baseline.txt"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project_root)
    rc = _MODULE.main(["--repo-root", str(project_root), "--update-baseline"])
    assert rc == 0
    body = baseline_path.read_text(encoding="utf-8")
    # Alpha sorts before Zeta; both must be present.
    alpha_idx = body.find("src/synthorg/alpha/errors.py")
    zeta_idx = body.find("src/synthorg/zeta/errors.py")
    assert alpha_idx >= 0
    assert zeta_idx >= 0
    assert alpha_idx < zeta_idx
