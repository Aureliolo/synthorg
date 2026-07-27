"""Unit tests for ``scripts/check_argument_count_suppression.py``.

The gate derives its candidate population from the AST and uses ``ruff`` only
to classify, so the tests cover both halves: that discovery finds what ``ruff``
would miss (a ``@override`` exemption, a file ``ruff`` never visits), and that
classification and the baseline behave once ``ruff`` has spoken.

Each test builds a throwaway project under ``tmp_path`` and lets the gate drive
the real pinned ``ruff`` over it, so the counting under test is the genuine
article rather than a reimplementation that could drift.

The subprocess-failure tests monkeypatch ``subprocess.run`` instead, because
the failure that matters most (ruff exiting 1 with blank stdout when it is not
importable, which is the same exit code as "violations found") cannot be
produced with a real ruff present.
"""

import importlib.util
import subprocess
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


class _StatusEnum(Protocol):
    UNSUPPRESSED: object
    PER_LINE: object
    BLANKET: object
    RULE_EXEMPT: object


class _CandidateView(Protocol):
    rel: str
    lineno: int
    qualname: str
    arg_count: int
    positional_count: int

    @property
    def key(self) -> str: ...


class _SiteView(Protocol):
    candidate: _CandidateView
    status: object

    @property
    def key(self) -> str: ...
    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _Site: type
    SiteStatus: _StatusEnum
    _MAX_ARGS_CEILING: int
    _MAX_POSITIONAL_ARGS: int
    RuffInvocationError: type[Exception]
    Candidate: type
    subprocess: object

    @staticmethod
    def _disables_rule(codes: object, rule: str) -> bool: ...
    @staticmethod
    def _load_baseline(path: Path) -> set[str]: ...
    @staticmethod
    def _parse_ruff_json(
        stdout: str, project_root: Path, pass_name: str
    ) -> list[tuple[str, int]]: ...
    @staticmethod
    def _scan(project_root: Path) -> list[_SiteView]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # sibling modules); restore sys.path so the load leaves no global side
    # effect that could shadow an unrelated import.
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

# Six parameters: over the sandbox arg cap of 5, under the ceiling of 8.
# Keyword-only so these breach the ARG cap alone; a positional signature this
# wide would also trip the separate positional pin and muddy what is asserted.
_WIDE_METHOD = """\
class Holder:
    def wide(self, *, alpha, beta, gamma, delta, epsilon, zeta):{marker}
        return alpha
"""
_WIDE_FUNCTION = """\
def wide_free(*, alpha, beta, gamma, delta, epsilon, zeta):{marker}
    return alpha
"""
_MARKER = "  # noqa: PLR0913"


@pytest.fixture(autouse=True)
def _pin_ruff_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own ruff cache.

    Every test in this file spawns ruff twice. Under ``-n 8`` an ambient
    ``RUFF_CACHE_DIR`` would have them all contend on one directory, which is
    a flake surface for no benefit.
    """
    monkeypatch.setenv("RUFF_CACHE_DIR", str(tmp_path / ".ruff_cache"))


def _write_project(
    root: Path,
    *,
    max_args: int = 5,
    max_positional: int = 5,
    lint_extra: str = "",
    ruff_extra: str = "",
) -> None:
    """Write a minimal ruff-configured project at *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f"[tool.ruff]\n{ruff_extra}\n"
        f"[tool.ruff.lint]\n{lint_extra}\n"
        "[tool.ruff.lint.pylint]\n"
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


def _write_baseline(root: Path, *entries: str) -> Path:
    """Write a baseline file containing *entries*."""
    path = root / _BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# header\n" + "\n".join(entries) + "\n", encoding="utf-8")
    return path


def _run(root: Path, *extra: str) -> int:
    """Invoke the gate's CLI against *root*."""
    return _MODULE.main(["--repo-root", str(root), *extra])


def _init_git(root: Path) -> None:
    """Make *root* a git repo with everything tracked.

    The gate enumerates its population with ``git ls-files``, so a sandbox
    needs a real index rather than a bare directory.
    """
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S607


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A sandbox project with the default cap pins and no source files."""
    _write_project(tmp_path)
    return tmp_path


class TestDiscoveryIsIndependentOfRuff:
    """The population comes from the AST, not from ruff's diagnostics."""

    def test_override_decorated_method_is_still_a_candidate(
        self,
        project: Path,
    ) -> None:
        # Ruff exempts @typing.override from PLR0913 syntactically, with no
        # base class required, so trusting its diagnostics would lose this
        # function entirely.
        _write_module(
            project,
            "pkg/m.py",
            "from typing import override\n\n\n"
            "class Holder:\n"
            "    @override\n"
            "    def wide(self, *, a, b, c, d, e, f):\n"
            "        return a\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert [s.candidate.qualname for s in sites] == ["Holder.wide"]
        assert sites[0].status is _MODULE.SiteStatus.RULE_EXEMPT

    def test_an_override_site_is_baselineable(self, project: Path) -> None:
        # It cannot carry a per-line marker: ruff never reports it, so the
        # marker would itself be dead. The baseline is its only record.
        _write_module(
            project,
            "pkg/m.py",
            "from typing import override\n\n\n"
            "class Holder:\n"
            "    @override\n"
            "    def wide(self, *, a, b, c, d, e, f):\n"
            "        return a\n",
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) == EXIT_OK

    def test_an_excluded_file_is_still_scanned(self, project: Path) -> None:
        # extend-exclude prunes ruff's walk, so both passes go quiet. The
        # AST population does not care.
        _write_project(project, ruff_extra='extend-exclude = ["pkg"]\n')
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=""))
        _init_git(project)

        assert _run(project) == EXIT_VIOLATION

    def test_positional_breach_under_the_arg_cap_is_a_candidate(
        self,
        project: Path,
    ) -> None:
        # Seven positional, seven total: under an arg cap of 8 but over the
        # positional pin of 5. This is the shape a PLR0917 per-file-ignore
        # would otherwise hide completely.
        _write_project(project, max_args=8, max_positional=5)
        _write_module(
            project,
            "pkg/m.py",
            "def wide(a, b, c, d, e, f, g):\n    return a\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert len(sites) == 1
        assert sites[0].candidate.positional_count == 7


class TestBaselineSubset:
    """A suppression is legal only when the baseline already names it."""

    def test_unbaselined_marker_fails(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _init_git(project)

        assert _run(project) == EXIT_VIOLATION

    def test_baselined_marker_passes(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) == EXIT_OK

    def test_under_cap_function_needs_no_entry(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", "def narrow(a, b, c):\n    return a\n")
        _init_git(project)

        assert _run(project) == EXIT_OK

    def test_unsuppressed_over_cap_function_fails(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=""))
        _init_git(project)

        assert _run(project) == EXIT_VIOLATION

    def test_widening_a_baselined_signature_needs_a_new_entry(
        self,
        project: Path,
    ) -> None:
        # The arity is part of the identity precisely so that growing an
        # approved signature costs a fresh approval instead of riding the
        # old entry.
        _write_module(
            project,
            "pkg/m.py",
            "class Holder:\n"
            "    def wide(self, *, a, b, c, d, e, f, g):  # noqa: PLR0913\n"
            "        return a\n",
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) != EXIT_OK


class TestKeyCollision:
    """One baseline entry must never authorise two functions."""

    def test_duplicate_qualname_is_rejected(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "class Config:\n"
            "    def build(self, *, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n"
            "    def build(self, *, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n",
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Config.build::6")

        assert _run(project) == EXIT_SETUP

    def test_collision_blocks_update_and_leaves_the_baseline_alone(
        self,
        project: Path,
    ) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "class Config:\n"
            "    def build(self, *, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n"
            "    def build(self, *, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n",
        )
        _init_git(project)
        path = _write_baseline(project, "pkg/other.py::gone::9")
        before = path.read_bytes()

        assert _run(project, "--update") == EXIT_SETUP
        assert path.read_bytes() == before


class TestBlanketSuppression:
    """A file-level blanket is rejected outright and can never be baselined."""

    def test_file_level_directive_fails(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "# ruff: noqa: PLR0913\n" + _WIDE_METHOD.format(marker=""),
        )
        _init_git(project)

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
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) != EXIT_OK

    def test_bare_file_level_directive_also_counts(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "# ruff: noqa\n" + _WIDE_METHOD.format(marker=""),
        )
        _init_git(project)

        assert _run(project) == EXIT_VIOLATION

    def test_per_file_ignores_entry_is_baselineable(self, tmp_path: Path) -> None:
        # Unlike a blanket, a per-file-ignores entry is a declared, reviewable
        # exemption, and discovery sees the function regardless. That is what
        # keeps the framework-shaped Litestar and pytest signatures on the
        # ledger rather than banned.
        _write_project(
            tmp_path,
            lint_extra='per-file-ignores = { "pkg/*.py" = ["PLR0913"] }\n',
        )
        _write_module(tmp_path, "pkg/m.py", _WIDE_METHOD.format(marker=""))
        _init_git(tmp_path)
        _write_baseline(tmp_path, "pkg/m.py::Holder.wide::6")

        assert _run(tmp_path) == EXIT_OK


class TestNoqaCaseVariants:
    """Ruff's keyword is case-insensitive; its rule codes are not."""

    def test_upper_case_keyword_is_a_valid_marker(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            _WIDE_METHOD.format(marker="  # NOQA: PLR0913"),
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) == EXIT_OK

    def test_lower_case_code_is_not_a_suppression(self, project: Path) -> None:
        # Ruff does not honour a lower-case code, so the site is simply
        # unsuppressed and must not pass on a baseline entry alone.
        _write_module(
            project,
            "pkg/m.py",
            _WIDE_METHOD.format(marker="  # noqa: plr0913"),
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) != EXIT_OK

    def test_multi_code_marker_is_recognised(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            _WIDE_METHOD.format(marker="  # noqa: D102, PLR0913"),
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) == EXIT_OK

    def test_marker_with_trailing_rationale_is_recognised(
        self,
        project: Path,
    ) -> None:
        _write_module(
            project,
            "pkg/m.py",
            _WIDE_METHOD.format(marker="  # noqa: PLR0913 -- orthogonal inputs"),
        )
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide::6")

        assert _run(project) == EXIT_OK


class TestConfigPins:
    """The cap itself, and where the configuration lives, are both held."""

    def test_default_pins_pass(self, project: Path) -> None:
        _init_git(project)

        assert _run(project) == EXIT_OK

    def test_max_args_above_the_ceiling_fails(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=_MODULE._MAX_ARGS_CEILING + 1)
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_max_args_at_the_ceiling_passes(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=_MODULE._MAX_ARGS_CEILING)
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_OK

    def test_lowering_max_args_is_allowed(self, tmp_path: Path) -> None:
        _write_project(tmp_path, max_args=3)
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_OK

    def test_boolean_max_args_is_rejected(self, tmp_path: Path) -> None:
        # bool subclasses int, so a bare isinstance check would let this pass
        # and compare below the ceiling.
        tmp_path.joinpath("pyproject.toml").write_text(
            "[tool.ruff.lint.pylint]\nmax-args = true\nmax-positional-args = 5\n",
            encoding="utf-8",
        )
        _init_git(tmp_path)

        assert _run(tmp_path) != EXIT_OK

    def test_missing_max_args_fails(self, tmp_path: Path) -> None:
        tmp_path.joinpath("pyproject.toml").write_text(
            "[tool.ruff.lint.pylint]\nmax-positional-args = 5\n",
            encoding="utf-8",
        )
        _init_git(tmp_path)

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
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_VIOLATION

    @pytest.mark.parametrize(
        "codes",
        ['["PLR0913"]', '["PL"]', '["PLR09"]', '["PLR0917"]'],
    )
    def test_ignoring_a_rule_wholesale_fails(
        self,
        tmp_path: Path,
        codes: str,
    ) -> None:
        _write_project(tmp_path, lint_extra=f"ignore = {codes}\n")
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_unrelated_ignore_entry_is_fine(self, tmp_path: Path) -> None:
        _write_project(tmp_path, lint_extra='ignore = ["PLR0912", "D100"]\n')
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_OK

    def test_extend_indirection_is_rejected(self, tmp_path: Path) -> None:
        # An extended base file could carry extend-ignore = ["PLR0913"] where
        # this gate would never look.
        _write_project(tmp_path, ruff_extra='extend = "base.toml"\n')
        tmp_path.joinpath("base.toml").write_text("", encoding="utf-8")
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_nested_ruff_config_is_rejected(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        nested = tmp_path / "pkg"
        nested.mkdir(parents=True, exist_ok=True)
        nested.joinpath("ruff.toml").write_text("", encoding="utf-8")
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_VIOLATION

    def test_missing_pyproject_fails_closed(self, tmp_path: Path) -> None:
        _init_git(tmp_path)

        assert _run(tmp_path) == EXIT_SETUP

    def test_repo_root_that_is_not_a_directory_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "a-file"
        target.write_text("", encoding="utf-8")

        assert _run(target) == EXIT_SETUP


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
        ids=[
            "exact",
            "family-prefix",
            "single-letter-prefix",
            "sibling-rule",
            "longer-than-rule",
            "empty-list",
            "bare-string",
            "none",
        ],
    )
    def test_prefix_matching(self, codes: object, expected: bool) -> None:
        assert _MODULE._disables_rule(codes, "PLR0913") is expected


class TestRuffInvocationFailures:
    """A scan that did not happen must never read as a clean tree."""

    def _break_ruff(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        returncode: int,
        stdout: str,
    ) -> None:
        real_run = subprocess.run

        def _fake_run(argv: list[str], **kwargs: object) -> object:
            # Only the ruff invocations are broken. The gate also shells out
            # to git to enumerate its population, and breaking that would
            # test a different failure entirely. Both spellings the gate can
            # resolve count: the venv console script it prefers, and the
            # `python -m ruff` fallback.
            invokes_ruff = "ruff" in argv or Path(argv[0]).stem == "ruff"
            if not invokes_ruff:
                return real_run(argv, **kwargs)  # type: ignore[call-overload]
            return subprocess.CompletedProcess(
                args=argv,
                returncode=returncode,
                stdout=stdout,
                stderr="ModuleNotFoundError: No module named 'ruff'",
            )

        monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run)

    def test_blank_stdout_on_exit_one_is_not_a_clean_tree(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Ruff exits 1 with blank stdout when it is not importable, which is
        # the same exit code as "violations found".
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=_MARKER))
        _init_git(project)
        self._break_ruff(monkeypatch, returncode=1, stdout="")

        with pytest.raises(_MODULE.RuffInvocationError):
            _MODULE._scan(project)

    def test_a_failed_scan_never_overwrites_the_baseline(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_module(project, "pkg/m.py", _WIDE_FUNCTION.format(marker=_MARKER))
        _init_git(project)
        path = _write_baseline(project, "pkg/real.py::important::9")
        before = path.read_bytes()
        self._break_ruff(monkeypatch, returncode=1, stdout="")

        assert _run(project, "--update") == EXIT_SETUP
        assert path.read_bytes() == before

    def test_a_failed_scan_does_not_pass_on_an_empty_baseline(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _init_git(project)
        self._break_ruff(monkeypatch, returncode=1, stdout="")

        assert _run(project) == EXIT_SETUP

    def test_unexpected_exit_code_is_reported(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _init_git(project)
        self._break_ruff(monkeypatch, returncode=2, stdout="")

        assert _run(project) == EXIT_SETUP


class TestParseRuffJson:
    """The JSON decoder fails closed on every malformed shape."""

    @pytest.mark.parametrize(
        "payload",
        ["", '{"not": "a list"}', "[1]", '[{"filename": "x.py"}]'],
        ids=["blank", "not-a-list", "item-not-a-dict", "missing-location"],
    )
    def test_malformed_payloads_raise(self, payload: str, tmp_path: Path) -> None:
        with pytest.raises(_MODULE.RuffInvocationError):
            _MODULE._parse_ruff_json(payload, tmp_path, "plain")

    def test_path_outside_the_project_root_raises(self, tmp_path: Path) -> None:
        outside = (tmp_path.parent / "elsewhere" / "x.py").as_posix()
        payload = f'[{{"filename": "{outside}", "location": {{"row": 1}}}}]'

        with pytest.raises(_MODULE.RuffInvocationError):
            _MODULE._parse_ruff_json(payload, tmp_path, "plain")

    def test_a_clean_run_decodes_to_no_sites(self, tmp_path: Path) -> None:
        assert _MODULE._parse_ruff_json("[]", tmp_path, "plain") == []


class TestQualnameResolution:
    """The baseline key must survive edits above the suppressed function."""

    def test_method_qualifies_with_its_class(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _init_git(project)

        sites = _MODULE._scan(project)

        assert [s.key for s in sites] == ["pkg/m.py::Holder.wide::6"]

    def test_nested_function_qualifies_with_its_parent(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "def outer():\n"
            "    def inner(*, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n"
            "    return inner\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert [s.key for s in sites] == ["pkg/m.py::outer.inner::6"]

    def test_method_inside_a_function_inside_a_class(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "class Outer:\n"
            "    def factory(self):\n"
            "        class Inner:\n"
            "            def make(self, *, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "                return a\n"
            "        return Inner\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert [s.key for s in sites] == [
            "pkg/m.py::Outer.factory.Inner.make::6",
        ]

    def test_static_method_does_not_lose_a_parameter(self, project: Path) -> None:
        _write_module(
            project,
            "pkg/m.py",
            "class Holder:\n"
            "    @staticmethod\n"
            "    def wide(*, a, b, c, d, e, f):  # noqa: PLR0913\n"
            "        return a\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert sites[0].candidate.arg_count == 6

    def test_multi_line_signature_resolves(self, project: Path) -> None:
        # Every real suppressed function in this repo has an exploded
        # signature, so the single-line fixtures elsewhere are not
        # representative on their own.
        _write_module(
            project,
            "pkg/m.py",
            "def wide(  # noqa: PLR0913\n"
            "    *,\n"
            "    alpha,\n"
            "    beta,\n"
            "    gamma,\n"
            "    delta,\n"
            "    epsilon,\n"
            "    zeta,\n"
            "):\n"
            "    return alpha\n",
        )
        _init_git(project)

        sites = _MODULE._scan(project)

        assert [s.key for s in sites] == ["pkg/m.py::wide::6"]
        assert sites[0].status is _MODULE.SiteStatus.PER_LINE

    def test_key_is_stable_across_an_edit_above_the_marker(
        self,
        project: Path,
    ) -> None:
        source = _WIDE_METHOD.format(marker=_MARKER)
        _write_module(project, "pkg/m.py", source)
        _init_git(project)
        before = [s.key for s in _MODULE._scan(project)]

        _write_module(project, "pkg/m.py", '"""Added above."""\n\n\n' + source)

        assert [s.key for s in _MODULE._scan(project)] == before


class TestBaselineFile:
    """Round-trip, validation, and drift detection."""

    def test_update_writes_only_baselineable_sites(self, project: Path) -> None:
        _write_module(project, "pkg/kept.py", _WIDE_METHOD.format(marker=_MARKER))
        _write_module(project, "pkg/bare.py", _WIDE_FUNCTION.format(marker=""))
        _init_git(project)

        assert _run(project, "--update") == EXIT_OK

        text = (project / _BASELINE_REL).read_text(encoding="utf-8")
        entries = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert entries == ["pkg/kept.py::Holder.wide::6"]

    def test_update_output_is_accepted_by_a_scan(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _init_git(project)

        assert _run(project, "--update") == EXIT_OK
        assert _run(project) == EXIT_OK

    def test_entries_are_sorted(self, project: Path) -> None:
        for name in ("zeta", "alpha", "mid"):
            _write_module(
                project,
                f"pkg/{name}.py",
                _WIDE_FUNCTION.format(marker=_MARKER),
            )
        _init_git(project)

        assert _run(project, "--update") == EXIT_OK

        text = (project / _BASELINE_REL).read_text(encoding="utf-8")
        entries = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert entries == sorted(entries)

    def test_stale_entry_fails_closed(self, project: Path) -> None:
        # An entry outliving its function would silently pre-authorise a
        # future suppression that happens to reuse the same identity.
        _init_git(project)
        _write_baseline(project, "pkg/gone.py::Vanished.method::9")

        assert _run(project) == EXIT_SETUP

    def test_malformed_entry_fails_closed(self, project: Path) -> None:
        _init_git(project)
        _write_baseline(project, "pkg/m.py::Holder.wide")

        assert _run(project) == EXIT_SETUP

    def test_duplicate_entry_fails_closed(self, project: Path) -> None:
        _write_module(project, "pkg/m.py", _WIDE_METHOD.format(marker=_MARKER))
        _init_git(project)
        _write_baseline(
            project,
            "pkg/m.py::Holder.wide::6",
            "pkg/m.py::Holder.wide::6",
        )

        assert _run(project) == EXIT_SETUP

    def test_absent_baseline_reads_as_empty(self, tmp_path: Path) -> None:
        assert _MODULE._load_baseline(tmp_path / "nope.txt") == set()

    def test_comments_and_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "b.txt"
        path.write_text(
            "# a comment\n\n  \npkg/m.py::Holder.wide::6\n",
            encoding="utf-8",
        )

        assert _MODULE._load_baseline(path) == {"pkg/m.py::Holder.wide::6"}


class TestSiteKeyGuard:
    """Only a baselineable site has a baseline identity."""

    def _site(self, status: object) -> _SiteView:
        return cast(
            _SiteView,
            _MODULE._Site(
                candidate=_MODULE.Candidate(
                    rel="pkg/m.py",
                    lineno=2,
                    qualname="Holder.wide",
                    arg_count=6,
                    positional_count=6,
                    over_arg_cap=True,
                    over_positional_cap=True,
                ),
                status=status,
            ),
        )

    @pytest.mark.parametrize("status_name", ["UNSUPPRESSED", "BLANKET"])
    def test_a_non_baselineable_site_refuses_to_mint_a_key(
        self,
        status_name: str,
    ) -> None:
        status = getattr(_MODULE.SiteStatus, status_name)

        with pytest.raises(ValueError, match="no baseline identity"):
            _ = self._site(status).key

    @pytest.mark.parametrize("status_name", ["PER_LINE", "RULE_EXEMPT"])
    def test_a_baselineable_site_mints_its_key(self, status_name: str) -> None:
        status = getattr(_MODULE.SiteStatus, status_name)

        assert self._site(status).key == "pkg/m.py::Holder.wide::6"
