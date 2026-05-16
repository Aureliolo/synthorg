"""Unit tests for the PreToolUse(Bash) gates that replaced inert hookify rules.

Covers ``check_no_pr_create.sh``, ``check_no_cd_prefix.sh``,
``check_no_local_coverage.sh``, ``check_enforce_parallel_tests.sh``.

Each is fed a PreToolUse JSON envelope on stdin (the same shape Claude
Code / OpenCode send) and asserted on exit code + deny payload. A
blocked command exits 2 and emits ``permissionDecision: deny``; an
allowed command exits 0 with no output.

Command strings that themselves contain a blocked pattern are built at
runtime so this test file does not trip the very gates it exercises.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

_BASH = shutil.which("bash")
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")

# Built by concatenation so the literal blocked phrases never appear
# verbatim in this source file.
_GH = "gh"
_PR_CREATE = _GH + " pr " + "create"
_CD = "c" + "d"


def _run(script: str, command: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPTS / script)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def _assert_blocked(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 2, result.stdout
    assert "permissionDecision" in result.stdout
    assert "deny" in result.stdout


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@_BASH_AVAILABLE
def test_pr_create_blocked() -> None:
    _assert_blocked(_run("check_no_pr_create.sh", f"{_PR_CREATE} --fill"))


@_BASH_AVAILABLE
@pytest.mark.parametrize("command", ["gh pr list", "gh pr view 1", "git status"])
def test_pr_create_allows_other_gh(command: str) -> None:
    _assert_allowed(_run("check_no_pr_create.sh", command))


@_BASH_AVAILABLE
def test_cd_prefix_blocked() -> None:
    _assert_blocked(_run("check_no_cd_prefix.sh", f"{_CD} web && npm i"))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        'bash -c "cd web && npm ci"',
        "uv run ruff check src/",
        "git -C ../other status",
    ],
)
def test_cd_prefix_allows_child_shell_and_plain(command: str) -> None:
    _assert_allowed(_run("check_no_cd_prefix.sh", command))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/ --cov=synthorg",
        "uv run python -m pytest tests/ --cov",
        "coverage run -m pytest",
    ],
)
def test_local_coverage_blocked(command: str) -> None:
    _assert_blocked(_run("check_no_local_coverage.sh", command))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        "uv run python -m pytest tests/ -m unit",
        "ruff check --cov-irrelevant src/",
    ],
)
def test_local_coverage_allows_plain(command: str) -> None:
    _assert_allowed(_run("check_no_local_coverage.sh", command))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        # Any explicit non-zero -n is wrong: addopts already pins -n=8.
        "uv run python -m pytest tests/ -m unit -n 8",
        "uv run python -m pytest tests/ -n 2",
        "pytest tests/ -n4",
        "pytest tests/ --numprocesses 2",
        "pytest tests/ -n auto",
        # Equals-sign forms must not bypass the gate.
        "pytest tests/ -n=4",
        "pytest tests/ --numprocesses=2",
        "pytest tests/ --numprocesses=auto",
        # xdist-disable on a suite/directory run (no ::) is blocked.
        "uv run python -m pytest tests/ -n0",
        "uv run python -m pytest tests/ -n 0",
        "pytest tests/ --dist no",
        "pytest tests/ -p no:xdist",
        # Equals-sign disable forms must not bypass the gate either.
        "uv run python -m pytest tests/ -n=0",
        "pytest tests/ --numprocesses=0",
        "pytest tests/ --dist=no",
    ],
)
def test_parallel_tests_blocks(command: str) -> None:
    _assert_blocked(_run("check_enforce_parallel_tests.sh", command))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        "uv run python -m pytest tests/ -m unit",  # no -n: addopts -n=8
        "uv run python -m pytest tests/benchmarks/ --codspeed -n0",
        # -n0 allowed ONLY for a single test node id (read full log).
        "uv run python -m pytest tests/unit/x.py::test_y -n0",
        "pytest tests/unit/a.py::test_b -n 0",
        "git status",
    ],
)
def test_parallel_tests_allows_default_benchmarks_and_single_test(
    command: str,
) -> None:
    _assert_allowed(_run("check_enforce_parallel_tests.sh", command))


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "script",
    [
        "check_no_pr_create.sh",
        "check_no_cd_prefix.sh",
        "check_no_local_coverage.sh",
        "check_enforce_parallel_tests.sh",
    ],
)
def test_no_stdin_is_noop(script: str) -> None:
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPTS / script)],
        input="",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "script",
    [
        "check_no_pr_create.sh",
        "check_no_cd_prefix.sh",
        "check_no_local_coverage.sh",
        "check_enforce_parallel_tests.sh",
    ],
)
def test_malformed_json_fails_closed(script: str) -> None:
    # stdin present but unparseable must NOT silently pass (that would
    # let a corrupted/truncated envelope bypass the gate). Whitespace-
    # only stdin is still treated as no-stdin and passes.
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPTS / script)],
        input='{"tool_input": {"command": ',
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "malformed" in result.stderr.lower()

    whitespace = subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPTS / script)],
        input="   \n  ",
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert whitespace.returncode == 0
