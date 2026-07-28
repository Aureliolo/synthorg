"""Unit tests for the ``scripts/git-hooks/_run-hook.sh`` wrapper.

The wrapper is what every commit and push in every worktree routes
through, yet nothing in the gate set executes its shell logic, so a
``set -e`` slip or a mis-ordered branch would only ever be discovered by
a developer whose push failed with no diagnostic at all.

The behaviour pinned here is the part that must survive that: the real
exit code always reaches git, the failure marker the re-push guard
depends on is written, the previous run's log is preserved, and the
five-minute budget is announced loudly when a push blows past it.

``pre_commit`` is never invoked. A stub ``uv`` on PATH stands in for the
whole hook run, so the tests exercise the wrapper's own logic in
milliseconds rather than re-running the gate suite.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "git-hooks" / "_run-hook.sh"


def _native_bash() -> str | None:
    """A ``bash`` that can execute a script given by its Windows path.

    On a default Windows PATH ``shutil.which("bash")`` finds WSL's
    ``system32\\bash.EXE`` first, and that bash resolves paths inside the
    Linux filesystem: handed ``C:\\...\\_run-hook.sh`` it strips the
    backslashes and exits 127 before running a line. Git Bash translates the
    same path, as does any POSIX bash, so the WSL shim is skipped rather than
    reported as a wrapper failure.

    Returns:
        The interpreter path, or ``None`` when only the WSL shim is present.
    """
    system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows")).resolve()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        found = shutil.which("bash", path=entry)
        if found is None:
            continue
        candidate = Path(found).resolve()
        if system_root in candidate.parents:
            continue
        return str(candidate)
    return None


_BASH = _native_bash()
_GIT = shutil.which("git")
_BASH_AVAILABLE = pytest.mark.skipif(
    _BASH is None or _GIT is None,
    reason="a non-WSL bash and git are both required",
)

# The wrapper compares whole elapsed seconds, so a sub-second test run is
# only "over budget" against a negative ceiling.
_ALWAYS_OVER_BUDGET = "-1"
_NEVER_OVER_BUDGET = "3600"

_STUB_UV = """#!/usr/bin/env bash
printf 'stub hook line one\\n'
printf 'stub hook line two\\n'
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confine every git call in this module to the per-test temp repo.

    ``git push`` exports ``GIT_DIR`` / ``GIT_INDEX_FILE`` / ``GIT_WORK_TREE``
    into the pre-push hook environment pytest inherits, and those override
    directory-based repo discovery. Left in place, the wrapper under test
    would resolve the REAL repository from inside the temp one: it would
    write its logs and its failure marker into the developer's own git
    dir, and the assertions here would read an empty temp tree.
    """
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    """Return a throwaway git repository with a stub ``uv`` on PATH."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _GIT is not None
    subprocess.run(  # noqa: S603
        [_GIT, "init", "--quiet"], cwd=repo, check=True, capture_output=True
    )
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(_STUB_UV, encoding="utf-8")
    stub.chmod(0o755)
    return repo


def _run_hook(
    repo: Path,
    *,
    hook_type: str = "pre-push",
    stub_exit: int = 0,
    budget: str = _NEVER_OVER_BUDGET,
    args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    stub_dir = repo.parent / "bin"
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_EXIT": str(stub_exit),
        "BUDGET_SECONDS": budget,
    }
    return subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPT), hook_type, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _log_dir(repo: Path) -> Path:
    return repo / ".git" / "synthorg-hooks"


@_BASH_AVAILABLE
def test_missing_hook_type_is_rejected(hook_repo: Path) -> None:
    assert _BASH is not None
    result = subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPT)],
        cwd=hook_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing hook-type argument" in result.stderr


@_BASH_AVAILABLE
@pytest.mark.parametrize("stub_exit", [0, 1, 42])
def test_the_hook_exit_code_reaches_git(hook_repo: Path, stub_exit: int) -> None:
    # Everything after the hook run reports the result rather than
    # producing it, so no amount of logging may rewrite the verdict.
    result = _run_hook(hook_repo, stub_exit=stub_exit)
    assert result.returncode == stub_exit


@_BASH_AVAILABLE
def test_every_run_records_its_duration(hook_repo: Path) -> None:
    # Without a recorded duration a gate-scope regression is only ever
    # felt, never seen.
    # The duration itself is whatever the run took; what must hold is that
    # one was recorded, in both streams.
    result = _run_hook(hook_repo)
    assert re.search(r"git pre-push hook: \d+m\d{2}s total", result.stdout)
    log = (_log_dir(hook_repo) / "pre-push-last.log").read_text(encoding="utf-8")
    assert re.search(r"hook: \d+m\d{2}s total", log)


@_BASH_AVAILABLE
def test_an_over_budget_run_says_so_loudly(hook_repo: Path) -> None:
    result = _run_hook(hook_repo, budget=_ALWAYS_OVER_BUDGET)
    # A green push that silently took a quarter of an hour is the exact
    # failure this banner exists to make impossible to miss.
    assert result.returncode == 0
    assert "OVER BUDGET" in result.stderr
    assert "gate-scope defect" in result.stderr
    # The banner is teed, not just printed: stderr scrolls away in a busy
    # terminal, so the durable log is where the overrun is read back later.
    log = (_log_dir(hook_repo) / "pre-push-last.log").read_text(encoding="utf-8")
    assert "OVER BUDGET" in log


@_BASH_AVAILABLE
def test_a_within_budget_run_stays_quiet(hook_repo: Path) -> None:
    result = _run_hook(hook_repo)
    assert "OVER BUDGET" not in result.stderr
    assert "OVER BUDGET" not in result.stdout


@_BASH_AVAILABLE
def test_a_failure_writes_the_marker_and_the_tail(hook_repo: Path) -> None:
    result = _run_hook(hook_repo, stub_exit=1)

    assert result.returncode == 1
    assert "git pre-push hook FAILED (exit 1)" in result.stderr
    # The re-push guard blocks on this marker until the log has been read.
    marker = _log_dir(hook_repo) / "pre-push-FAILED"
    assert "status=1" in marker.read_text(encoding="utf-8")
    # The failing tail is re-emitted so a truncated terminal still shows
    # the actionable signal.
    assert "stub hook line two" in result.stderr


@_BASH_AVAILABLE
def test_a_clean_run_clears_a_stale_marker(hook_repo: Path) -> None:
    _run_hook(hook_repo, stub_exit=1)
    marker = _log_dir(hook_repo) / "pre-push-FAILED"
    assert marker.exists()

    assert _run_hook(hook_repo, stub_exit=0).returncode == 0
    assert not marker.exists()


@_BASH_AVAILABLE
def test_the_previous_runs_log_survives_a_re_push(hook_repo: Path) -> None:
    # Re-pushing after a failure would otherwise overwrite the only
    # diagnostic of what went wrong.
    _run_hook(hook_repo, stub_exit=1)
    first = (_log_dir(hook_repo) / "pre-push-last.log").read_text(encoding="utf-8")
    _run_hook(hook_repo, stub_exit=0)

    assert (_log_dir(hook_repo) / "pre-push-prev.log").read_text(
        encoding="utf-8"
    ) == first


@_BASH_AVAILABLE
def test_an_over_budget_failure_reports_both(hook_repo: Path) -> None:
    # The budget banner must not consume the failure, nor the failure the
    # budget: a slow push that also failed has two things to fix.
    result = _run_hook(hook_repo, stub_exit=1, budget=_ALWAYS_OVER_BUDGET)

    assert result.returncode == 1
    assert "OVER BUDGET" in result.stderr
    assert "FAILED (exit 1)" in result.stderr


@_BASH_AVAILABLE
def test_each_hook_type_keeps_its_own_log(hook_repo: Path) -> None:
    _run_hook(hook_repo, hook_type="pre-commit", stub_exit=1)
    _run_hook(hook_repo, hook_type="pre-push", stub_exit=0)

    # A clean push must not clear a failed commit's marker: they gate
    # different operations.
    assert (_log_dir(hook_repo) / "pre-commit-FAILED").exists()
    assert not (_log_dir(hook_repo) / "pre-push-FAILED").exists()
