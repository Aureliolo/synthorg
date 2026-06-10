"""Self-tests for the ``no-synthorg-any-override`` regression gate.

Pins the gate contract: no ``[[tool.mypy.overrides]]`` block targeting a
``synthorg`` module may lift ``disallow_any_explicit`` -- whether via
``disallow_any_explicit = false``, ``explicit-any`` in ``disable_error_code``,
or ``ignore_errors = true``, and whether the module is named exactly, via a
dotted trailing wildcard, or via a leading-``*`` glob (``*.api``) that mypy
compiles to a dot-spanning match. Only the ``tests.*`` override may lift the flag.
"""

import importlib.util
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _REPO_ROOT / "scripts" / "check_no_synthorg_any_override.py"


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location(
        "_no_synthorg_any_override_gate",
        _GATE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()


def _violations(toml_text: str) -> list[str]:
    result = _GATE.find_violations(tomllib.loads(toml_text))  # type: ignore[attr-defined]
    assert isinstance(result, list)
    return result


def _run_main(tmp_path: Path, toml_text: str) -> int:
    (tmp_path / "pyproject.toml").write_text(toml_text, encoding="utf-8")
    rc = _GATE.main(["--repo-root", str(tmp_path)])  # type: ignore[attr-defined]
    assert isinstance(rc, int)
    return rc


# Each case is a full ``[[tool.mypy.overrides]]`` snippet paired with the
# expected ``find_violations`` result, so a regression in any single detection
# branch flips exactly one row.
_FIND_VIOLATIONS_CASES = [
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "tests.*"\ndisallow_any_explicit = false\n',
        [],
        id="tests_override_allowed",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.engine.*"\n'
        "disallow_any_explicit = true\n",
        [],
        id="synthorg_enforced_true_ignored",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\n'
        'disable_error_code = ["unused-awaitable"]\n',
        [],
        id="other_disable_code_list_ignored",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\n'
        'disable_error_code = "unused-awaitable"\n',
        [],
        id="other_disable_code_str_ignored",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "litellm.*"\n'
        "ignore_missing_imports = true\n",
        [],
        id="unrelated_override_ignored",
    ),
    pytest.param(
        "[tool.mypy]\ndisallow_any_explicit = true\n",
        [],
        id="no_overrides_section",
    ),
    pytest.param(
        "[[tool.mypy.overrides]]\ndisallow_any_explicit = false\n",
        [],
        id="block_without_module_key",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.engine.*"\n'
        "disallow_any_explicit = false\n",
        ["synthorg.engine.*"],
        id="single_module_false",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg"\ndisallow_any_explicit = false\n',
        ["synthorg"],
        id="bare_synthorg_exact",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = ["synthorg.api.*", "synthorg.meta.*"]\n'
        "disallow_any_explicit = false\n",
        ["synthorg.api.*", "synthorg.meta.*"],
        id="list_form_two_patterns",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = ["synthorg.engine.*", "tests.*"]\n'
        "disallow_any_explicit = false\n",
        ["synthorg.engine.*"],
        id="mixed_synthorg_and_tests_flags_only_synthorg",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\n'
        'disable_error_code = ["explicit-any", "unused-awaitable"]\n',
        ["synthorg.api.*"],
        id="disable_code_list_explicit_any",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\n'
        'disable_error_code = "explicit-any"\n',
        ["synthorg.api.*"],
        id="disable_code_str_explicit_any",
    ),
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "*"\ndisallow_any_explicit = false\n',
        ["*"],
        id="catch_all_glob",
    ),
    # mypy treats a ``*`` glued to other characters as a *literal* asterisk
    # (``compile_glob`` escapes it), so ``synthorg*`` matches no real module and
    # cannot lift the flag for synthorg code -- correctly NOT a violation.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg*"\n'
        "disallow_any_explicit = false\n",
        [],
        id="glued_star_is_literal_not_a_violation",
    ),
    # An adjacent unrelated package must never be flagged: the dotted
    # ``synthorg.`` prefix excludes ``synthorgX`` even under a flag-lift.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorgX.*"\n'
        "disallow_any_explicit = false\n",
        [],
        id="adjacent_package_not_flagged",
    ),
    # ``ignore_errors = true`` silences every error -- explicit-any included --
    # so it lifts the flag just like ``disallow_any_explicit = false``.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\nignore_errors = true\n',
        ["synthorg.api.*"],
        id="ignore_errors_true_lifts",
    ),
    # ``ignore_errors = false`` lifts nothing.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "synthorg.api.*"\nignore_errors = false\n',
        [],
        id="ignore_errors_false_ignored",
    ),
    # ``ignore_errors = true`` on a third-party module does not target synthorg.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "litellm.*"\nignore_errors = true\n',
        [],
        id="ignore_errors_true_third_party_ignored",
    ),
    # A leading-``*`` glob compiles to a dot-spanning ``.*`` and so matches
    # ``synthorg.api`` -- the suffix-glob bypass the literal-prefix check misses.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "*.api"\ndisallow_any_explicit = false\n',
        ["*.api"],
        id="wildcard_suffix_targets_synthorg",
    ),
    # A nested leading-``*`` glob (``*.x.*``) likewise spans the synthorg prefix.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "*.controllers.*"\n'
        "disallow_any_explicit = false\n",
        ["*.controllers.*"],
        id="wildcard_nested_targets_synthorg",
    ),
    # Any leading-``*`` flag-lift is blocked even when its suffix names no real
    # synthorg module: such a dot-spanning wildcard is either project-wide
    # weakening or a no-op misconfiguration, and over-matching here never lets a
    # real synthorg lift slip through.
    pytest.param(
        '[[tool.mypy.overrides]]\nmodule = "*.not_a_real_pkg"\n'
        "disallow_any_explicit = false\n",
        ["*.not_a_real_pkg"],
        id="leading_star_blocked_even_on_unknown_suffix",
    ),
]


@pytest.mark.parametrize(("toml_text", "expected"), _FIND_VIOLATIONS_CASES)
def test_find_violations(toml_text: str, expected: list[str]) -> None:
    assert _violations(toml_text) == expected


def test_main_returns_zero_on_clean_pyproject(tmp_path: Path) -> None:
    """``main`` exits 0 when only the ``tests.*`` override lifts the flag."""
    clean = (
        '[[tool.mypy.overrides]]\nmodule = "tests.*"\ndisallow_any_explicit = false\n'
    )
    assert _run_main(tmp_path, clean) == 0


def test_main_returns_one_on_synthorg_override(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 1 and names the offending module on a synthorg override."""
    bad = (
        '[[tool.mypy.overrides]]\nmodule = "synthorg.engine.*"\n'
        "disallow_any_explicit = false\n"
    )
    assert _run_main(tmp_path, bad) == 1
    stderr = capsys.readouterr().err
    assert "synthorg.engine.*" in stderr
    assert "disallow_any_explicit" in stderr


def test_main_returns_two_on_missing_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 2 (config error) when ``pyproject.toml`` is absent."""
    rc = _GATE.main(["--repo-root", str(tmp_path)])  # type: ignore[attr-defined]
    assert rc == 2
    assert "could not read pyproject.toml" in capsys.readouterr().err


def test_main_returns_two_on_malformed_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 2 (config error) on an unparseable ``pyproject.toml``."""
    rc = _run_main(tmp_path, "this is not valid toml ][")
    assert rc == 2
    assert "could not parse pyproject.toml" in capsys.readouterr().err


def test_main_returns_two_on_unreadable_repo_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits 2 when ``--repo-root`` is not a directory."""
    missing = tmp_path / "does-not-exist"
    rc = _GATE.main(["--repo-root", str(missing)])  # type: ignore[attr-defined]
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_real_pyproject_is_compliant() -> None:
    """The gate must be green against the actual repo pyproject (no regressions)."""
    assert _GATE.main(["--repo-root", str(_REPO_ROOT)]) == 0  # type: ignore[attr-defined]


def test_gate_model_matches_mypy_semantics() -> None:
    """Pin the gate's synthorg-targeting rule to how mypy actually applies a
    ``[[tool.mypy.overrides]]`` ``module`` pattern.

    The gate flags a leading-``*`` glob (which mypy compiles to a dot-spanning
    ``.*``) and a literal ``synthorg`` prefix, but not a ``*`` glued to other
    characters (which mypy treats as a literal asterisk). A failure here means
    mypy's per-module glob semantics changed and the gate rule in
    ``_targets_synthorg`` must be re-derived -- it does NOT mean the gate logic
    regressed.
    """
    from mypy.options import Options

    def mypy_applies(pattern: str, module: str) -> bool:
        opts = Options()
        opts.disallow_any_explicit = True
        opts.per_module_options[pattern] = {"disallow_any_explicit": False}
        opts.build_per_module_cache()
        return opts.clone_for_module(module).disallow_any_explicit is False

    # Leading-``*`` glob spans the synthorg prefix -> mypy applies, gate flags.
    assert mypy_applies("*.api", "synthorg.api")
    assert _GATE._targets_synthorg("*.api")  # type: ignore[attr-defined]
    # Literal dotted prefix -> mypy applies, gate flags.
    assert mypy_applies("synthorg.*", "synthorg.api")
    assert _GATE._targets_synthorg("synthorg.*")  # type: ignore[attr-defined]
    # Glued ``*`` is a literal asterisk -> mypy does NOT apply, gate does NOT flag.
    assert not mypy_applies("synthorg*", "synthorg.api")
    assert not _GATE._targets_synthorg("synthorg*")  # type: ignore[attr-defined]
