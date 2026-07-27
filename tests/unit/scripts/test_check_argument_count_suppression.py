"""Unit tests for ``scripts/check_argument_count_suppression.py``.

Exercises the four invariants the gate holds: the ``max-args`` /
``max-positional-args`` config pins, the rejection of wholesale PLR0913
disabling, the per-line-versus-blanket suppression classification, and the
baseline subset check with its stale-entry drift detection.

Each test builds a throwaway project under ``tmp_path`` and lets the gate
drive the real pinned ``ruff`` over it, so the argument counting under test
is ruff's own rather than a reimplementation that could drift from it.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_cost_scope_purpose.py``.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_argument_count_suppression.py"

_BASELINE_REL = "scripts/argument_count_suppression_baseline.txt"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_SETUP = 2


class _SiteView(Protocol):
    rel: str
    lineno: int
    qualname: str

    def baseline_key(self) -> str: ...
    def message(self) -> str: ...


class _SuppressionEnum(Protocol):
    PER_LINE: object
    BLANKET: object
    NONE: object


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _Site: type
    Suppression: _SuppressionEnum
    _MAX_ARGS_CEILING: int
    _MAX_POSITIONAL_ARGS: int

    @staticmethod
    def _disables_rule(codes: object) -> bool: ...
    @staticmethod
    def _load_baseline(path: Path) -> set[str]: ...
    @staticmethod
    def _scan(project_root: Path) -> list[_SiteView]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_argument_count_suppression",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()

# A six-parameter method: over the sandbox cap of 5, under the ceiling of 8.
_WIDE_METHOD = """\
class Holder:
    def wide(self, alpha, beta, gamma, delta, epsilon, zeta):{marker}
        return alpha
"""
_WIDE_FUNCTION = """\
def wide_free(alpha, beta, gamma, delta, epsilon, zeta):{marker}
    return alpha
"""
_MARKER = "  # noqa: PLR0913"


def _write_project(
    root: Path,
    *,
    max_args: int = 5,
    max_positional: int = 5,
    lint_extra: str = "",
) -> None:
    """Write a minimal ruff-configured project at *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[tool.ruff.lint]\n"
        f"{lint_extra}"
        "\n[tool.ruff.lint.pylint]\n"
        f"max-args = {max_args}\n"
        f"max-positional-args = {max_positional}\n",
        encoding="utf-8",
    )


def _write_module(root: Path, rel: str, source: str) -> Path:
    """Write *source* to ``root / rel``, creating parents."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _write_baseline(root: Path, *entries: str) -> None:
    """Write a baseline file containing *entries*."""
    path = root / _BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# header\n" + "\n".join(entries) + "\n", encoding="utf-8")


def _run(root: Path, *extra: str) -> int:
    """Invoke the gate's CLI against *root*."""
    return _MODULE.main(["--repo-root", str(root), *extra])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A sandbox project with the default cap pins and no source files."""
    _write_project(tmp_path)
    return tmp_path


class TestBaselineSubset:
    """A per-line marker is legal only when the baseline already names it."""

    def test_unbaselined_marker_fails(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))

        assert _run(project) == EXIT_VIOLATION

    def test_baselined_marker_passes(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _write_baseline(project, "pkg/m.py::Holder.wide")

        assert _run(project) == EXIT_OK

    def test_under_cap_function_needs_no_entry(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", "def narrow(a, b, c):\n    return a\n")

        assert _run(project) == EXIT_OK

    def test_unsuppressed_over_cap_function_fails(self, project: Path) -> None:
        # No marker at all: ruff reports it directly, and the gate must not
        # let it through just because the baseline cannot cover it.
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=""))

        assert _run(project) == EXIT_VIOLATION

    def test_a_baseline_entry_cannot_launder_an_unsuppressed_site(
        self,
        project: Path,
    ) -> None:
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=""))
        _write_baseline(project, "pkg/m.py::wide_free")

        # The entry is stale (no per-line marker maps to it), which is the
        # louder of the two failures and reports first.
        assert _run(project) == EXIT_SETUP


class TestBlanketSuppression:
    """A blanket exemption is rejected outright; it can never be baselined."""

    def test_file_level_directive_fails(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "# ruff: noqa: PLR0913\n" + _WIDE_METHOD.format(marker=""),
        )

        assert _run(project) == EXIT_VIOLATION

    def test_file_level_directive_fails_even_when_baselined(
        self,
        project: Path,
    ) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "# ruff: noqa: PLR0913\n" + _WIDE_METHOD.format(marker=""),
        )
        _write_baseline(project, "pkg/m.py::Holder.wide")

        assert _run(project) == EXIT_SETUP

    def test_per_file_ignores_entry_fails(self, tmp_path: Path) -> None:
        _write_project(
            tmp_path,
            lint_extra='per-file-ignores = { "pkg/*.py" = ["PLR0913"] }\n',
        )
        _write_module(tmp_path, "pkg/m.py", _WIDE_METHOD.format(marker=""))

        assert _run(tmp_path) == EXIT_VIOLATION


class TestConfigPins:
    """The cap itself is part of what the gate holds."""

    def test_default_pins_pass(self, project: Path) -> None:
        assert _run(project) == EXIT_OK

    def test_max_args_above_the_ceiling_fails(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=_MODULE._MAX_ARGS_CEILING + 1)

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_max_args_at_the_ceiling_passes(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=_MODULE._MAX_ARGS_CEILING)

        assert _run(tmp_path) == EXIT_OK

    def test_lowering_max_args_is_allowed(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=3)

        assert _run(tmp_path) == EXIT_OK

    def test_missing_max_args_fails(self, tmp_path: Path) -> None:
        tmp_path.joinpath("pyproject.toml").write_text(
            "[tool.ruff.lint.pylint]\nmax-positional-args = 5\n",
            encoding="utf-8",
        )

        assert _run(tmp_path) == EXIT_VIOLATION

    @pytest.mark.parametrize("positional", [4, 6, 8])
    def test_max_positional_args_off_the_pin_fails(
        self,
        tmp_path: Path,
        positional: int,
    ) -> None:
        # Pinned exactly, not as a ceiling: ruff defaults it to max-args, so
        # leaving it loose lets the positional cap widen by inheritance.
        _write_project(tmp_path, max_positional=positional)

        assert _run(tmp_path) == EXIT_VIOLATION

    @pytest.mark.parametrize("codes", ['["PLR0913"]', '["PL"]', '["PLR09"]'])
    def test_ignoring_the_rule_wholesale_fails(
        self,
        tmp_path: Path,
        codes: str,
    ) -> None:
        _write_project(tmp_path, lint_extra=f"ignore = {codes}\n")

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_unrelated_ignore_entry_is_fine(self, tmp_path: Path) -> None:
        _write_project(tmp_path, lint_extra='ignore = ["PLR0912", "D100"]\n')

        assert _run(tmp_path) == EXIT_OK

    def test_missing_pyproject_fails_closed(self, tmp_path: Path) -> None:
        assert _run(tmp_path) == EXIT_SETUP

    def test_repo_root_that_is_not_a_directory_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "a-file"
        target.write_text("", encoding="utf-8")

        assert _MODULE.main(["--repo-root", str(target)]) == EXIT_SETUP


class TestDisablesRule:
    """Prefix semantics: a shorter selector silences everything under it."""

    @pytest.mark.parametrize(
        ("codes", "expected"),
        [
            (["PLR0913"], True),
            (["PL"], True),
            (["P"], True),
            (["PLR0912"], False),
            (["PLR09131"], False),
            ([], False),
            ("PLR0913", False),
            (None, False),
        ],
    )
    def test_prefix_matching(
        self,
        codes: object,
        expected: bool,
    ) -> None:
        assert _MODULE._disables_rule(codes) is expected


class TestQualnameResolution:
    """The baseline key must survive edits above the suppressed function."""

    def test_method_qualifies_with_its_class(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))

        sites = _MODULE._scan(project)

        assert [s.baseline_key() for s in sites] == ["pkg/m.py::Holder.wide"]

    def test_nested_function_qualifies_with_its_parent(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "def outer():\n"
            "    def inner(a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n"
            "    return inner\n",
        )

        sites = _MODULE._scan(project)

        assert [s.baseline_key() for s in sites] == ["pkg/m.py::outer.inner"]

    def test_decorated_async_method_resolves(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "import functools\n\n\n"
            "class Holder:\n"
            "    @functools.cache\n"
            "    async def wide(self, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n",
        )

        sites = _MODULE._scan(project)

        assert [s.baseline_key() for s in sites] == ["pkg/m.py::Holder.wide"]

    def test_key_is_stable_across_an_edit_above_the_marker(
        self,
        project: Path,
    ) -> None:
        source = _WIDE_METHOD.format(marker=_MARKER)
        _write_module(project, "pkg/m.py", source)
        before = [s.baseline_key() for s in _MODULE._scan(project)]

        _write_module(project, "pkg/m.py", '"""Added above."""\n\n\n' + source)

        assert [s.baseline_key() for s in _MODULE._scan(project)] == before


class TestBaselineFile:
    """Round-trip, validation, and drift detection."""

    def test_update_writes_only_per_line_sites(self, project: Path) -> None:
        _write_module(project, "pkg/kept.py", _WIDE_METHOD.format(marker=_MARKER))
        _write_module(project, "pkg/bare.py", _WIDE_FUNCTION.format(marker=""))

        assert _run(project, "--update") == EXIT_OK

        text = (project / _BASELINE_REL).read_text(encoding="utf-8")
        entries = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert entries == ["pkg/kept.py::Holder.wide"]

    def test_update_output_is_accepted_by_a_scan(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))

        assert _run(project, "--update") == EXIT_OK
        assert _run(project) == EXIT_OK

    def test_stale_entry_fails_closed(self, project: Path) -> None:
        # An entry outliving its function would silently pre-authorise a
        # future suppression that happens to reuse the same name.
        _write_baseline(project, "pkg/gone.py::Vanished.method")

        assert _run(project) == EXIT_SETUP

    def test_malformed_entry_fails_closed(self, project: Path) -> None:
        _write_baseline(project, "pkg/m.py:12:4")

        assert _run(project) == EXIT_SETUP

    def test_duplicate_entry_fails_closed(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _write_baseline(
            project,
            "pkg/m.py::Holder.wide",
            "pkg/m.py::Holder.wide",
        )

        assert _run(project) == EXIT_SETUP

    def test_absent_baseline_reads_as_empty(self, tmp_path: Path) -> None:
        assert _MODULE._load_baseline(tmp_path / "nope.txt") == set()

    def test_comments_and_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "b.txt"
        path.write_text(
            "# a comment\n\n  \npkg/m.py::Holder.wide\n",
            encoding="utf-8",
        )

        assert _MODULE._load_baseline(path) == {"pkg/m.py::Holder.wide"}


class TestSiteValidation:
    """A malformed site must fail at construction, not at round-trip."""

    @pytest.mark.parametrize(
        ("rel", "lineno", "qualname"),
        [
            ("", 1, "f"),
            ("pkg/m.py", 0, "f"),
            ("pkg/m.py", -1, "f"),
            ("pkg/m.py", 1, ""),
        ],
    )
    def test_invalid_coordinates_raise(
        self,
        rel: str,
        lineno: int,
        qualname: str,
    ) -> None:
        with pytest.raises(ValueError, match="must"):
            _MODULE._Site(
                rel=rel,
                lineno=lineno,
                qualname=qualname,
                suppression=_MODULE.Suppression.PER_LINE,
            )
