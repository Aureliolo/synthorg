"""Self-tests for the ``no-explicit-any-inline-disable`` regression gate.

Pins the gate contract: no module-level ``# mypy:`` comment under ``src/`` or
``tests/`` may lift ``disallow_any_explicit`` or ``unused-ignore`` -- whether via
``disable-error-code``, a ``disallow-any-explicit = False`` /
``warn-unused-ignores = False`` boolean, or ``ignore-errors``. The sanctioned
per-line ``# type: ignore[explicit-any]`` escape hatch and file-level disables of
unrelated codes (``union-attr``, ``arg-type``, ...) must NOT be flagged.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _REPO_ROOT / "scripts" / "check_no_explicit_any_inline_disable.py"


def _load_gate() -> object:
    spec = importlib.util.spec_from_file_location(
        "_no_explicit_any_inline_disable_gate",
        _GATE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()


def _scan(text: str) -> list[tuple[int, str]]:
    result = _GATE.scan_text(text)  # type: ignore[attr-defined]
    assert isinstance(result, list)
    return result


# Each case pairs a single source line with whether the gate must flag it, so a
# regression in any single detection branch flips exactly one row.
_FLAGGED_CASES = [
    pytest.param(
        '# mypy: disable-error-code="explicit-any"', id="disable_explicit_any"
    ),
    pytest.param(
        '# mypy: disable-error-code="unused-ignore"', id="disable_unused_ignore"
    ),
    pytest.param("# mypy: disable-error-code=explicit-any", id="disable_bare_unquoted"),
    pytest.param(
        '# mypy: disable-error-code="union-attr,explicit-any"',
        id="disable_explicit_any_among_others",
    ),
    pytest.param(
        "# mypy: disallow-any-explicit = False", id="disallow_flag_dash_false"
    ),
    pytest.param(
        "# mypy: disallow_any_explicit = False", id="disallow_flag_underscore_false"
    ),
    pytest.param("# mypy: warn-unused-ignores = False", id="warn_unused_ignores_false"),
    pytest.param("# mypy: ignore-errors", id="ignore_errors_flag_only"),
    pytest.param("# mypy: ignore-errors = True", id="ignore_errors_true"),
    pytest.param(
        "# mypy: disable-error-code='explicit-any'", id="disable_single_quoted"
    ),
    # mypy parses inline booleans via configparser: no/off/0 are falsy, yes/on/1
    # truthy. A falsy disallow-flag lifts; a truthy ignore-errors silences.
    pytest.param("# mypy: disallow-any-explicit = no", id="disallow_flag_no"),
    pytest.param("# mypy: disallow-any-explicit = off", id="disallow_flag_off"),
    pytest.param("# mypy: disallow-any-explicit = 0", id="disallow_flag_0"),
    pytest.param("# mypy: warn-unused-ignores = NO", id="warn_unused_ignores_NO"),
    pytest.param("# mypy: ignore-errors = yes", id="ignore_errors_yes"),
    pytest.param("# mypy: ignore-errors = on", id="ignore_errors_on"),
    pytest.param("# mypy: ignore-errors = 1", id="ignore_errors_1"),
    # mypy accepts space-separated codes (bare and quoted), not just commas.
    pytest.param(
        "# mypy: disable-error-code=union-attr explicit-any",
        id="disable_space_separated_bare",
    ),
    pytest.param(
        '# mypy: disable-error-code="union-attr explicit-any"',
        id="disable_space_separated_quoted",
    ),
    # A benign first ``disable-error-code`` directive must not mask a lifting
    # second one: the scan inspects every occurrence, not just the first.
    pytest.param(
        "# mypy: disable-error-code=union-attr, disable-error-code=explicit-any",
        id="disable_second_directive_lifts",
    ),
]

_CLEAN_CASES = [
    pytest.param(
        "x: Any = 1  # type: ignore[explicit-any]  # reason", id="per_line_ignore"
    ),
    pytest.param(
        '# mypy: disable-error-code="union-attr,method-assign"', id="other_codes"
    ),
    pytest.param("# mypy: disable-error-code=arg-type", id="other_code_bare"),
    pytest.param("# mypy: disallow-any-explicit = True", id="disallow_flag_true"),
    pytest.param("# mypy: ignore-errors = False", id="ignore_errors_false"),
    pytest.param('# mypy: disable-error-code="empty-body"', id="empty_body"),
    pytest.param("def f(x: object) -> None: ...", id="plain_code"),
    pytest.param("# a comment mentioning explicit-any in prose", id="prose_mention"),
    # A directive inside a STRING literal is a string token, not a comment
    # token; the tokenize-based scan must never flag it (this gate's own
    # fixtures embed such strings).
    pytest.param('s = "# mypy: ignore-errors"', id="directive_inside_string_literal"),
    # A truthy disallow-flag ENFORCES (does not lift); a falsy ignore-errors
    # silences nothing.
    pytest.param("# mypy: ignore-errors = no", id="ignore_errors_no"),
    pytest.param("# mypy: ignore-errors = 0", id="ignore_errors_0"),
    pytest.param("# mypy: warn-unused-ignores = True", id="warn_unused_ignores_true"),
]


@pytest.mark.parametrize("line", _FLAGGED_CASES)
def test_flagged_lines_are_violations(line: str) -> None:
    violations = _scan(line)
    assert len(violations) == 1
    lineno, reason = violations[0]
    assert lineno == 1
    # Pin the reason so a branch cross-wire (input A matched by branch B, both
    # yielding one violation) cannot pass silently.
    assert reason, "violation must carry a non-empty reason"
    assert any(
        token in reason
        for token in ("explicit-any", "unused-ignore", "disallow", "ignore-errors")
    ), reason


@pytest.mark.parametrize("line", _CLEAN_CASES)
def test_clean_lines_are_not_violations(line: str) -> None:
    assert _scan(line) == []


def test_syntax_error_yields_no_violations() -> None:
    """A file mypy itself rejects does not tokenise; the scan returns []."""
    assert _scan("def f(\n# mypy: ignore-errors\n") == []


def test_line_numbers_are_reported() -> None:
    text = '"""module."""\nimport os\n# mypy: disable-error-code="explicit-any"\n'
    violations = _scan(text)
    assert len(violations) == 1
    assert violations[0][0] == 3


def test_main_returns_zero_on_clean_file(tmp_path: Path) -> None:
    target = tmp_path / "clean.py"
    target.write_text(
        "x = 1  # type: ignore[explicit-any]  # reason\n", encoding="utf-8"
    )
    rc = _GATE.main([str(target)])  # type: ignore[attr-defined]
    assert rc == 0


def test_main_returns_one_and_names_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "bad.py"
    target.write_text('# mypy: disable-error-code="explicit-any"\n', encoding="utf-8")
    rc = _GATE.main([str(target)])  # type: ignore[attr-defined]
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "bad.py" in stderr
    assert "explicit-any" in stderr


def test_main_ignores_non_python_paths(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('# mypy: disable-error-code="explicit-any"\n', encoding="utf-8")
    rc = _GATE.main([str(target)])  # type: ignore[attr-defined]
    assert rc == 0


def test_main_returns_two_on_unreadable_repo_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does-not-exist"
    rc = _GATE.main(["--repo-root", str(missing)])  # type: ignore[attr-defined]
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_real_repo_files_are_clean() -> None:
    """End-to-end on real repo files (drained sources) without a 5000-file walk.

    The whole-tree scan belongs to the pre-push gate invocation; this passes a
    bounded set of real, drained files so the gate's file-read + scan path is
    exercised against actual source, fast.
    """
    real_files = [
        str(_GATE_PATH),
        str(_REPO_ROOT / "scripts" / "check_no_synthorg_any_override.py"),
        str(_REPO_ROOT / "tests" / "_shared" / "json_types.py"),
        str(_REPO_ROOT / "tests" / "unit" / "tools" / "conftest.py"),
    ]
    rc = _GATE.main(real_files)  # type: ignore[attr-defined]
    assert rc == 0
