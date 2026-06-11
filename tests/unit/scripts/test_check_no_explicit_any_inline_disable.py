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
]


@pytest.mark.parametrize("line", _FLAGGED_CASES)
def test_flagged_lines_are_violations(line: str) -> None:
    violations = _scan(line)
    assert len(violations) == 1
    assert violations[0][0] == 1


@pytest.mark.parametrize("line", _CLEAN_CASES)
def test_clean_lines_are_not_violations(line: str) -> None:
    assert _scan(line) == []


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
