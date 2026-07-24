"""Guard: no test may inherit git's repo-discovery environment.

Git honours ``GIT_DIR`` over the ``cwd`` a subprocess is handed, and a
pre-push hook exports it pointing at the real repository. Without the
repo-wide scrub in ``tests/conftest.py`` a test that shells out to git
operates on this checkout instead of its own fixture, and ``git init
--bare`` under an inherited ``GIT_DIR`` writes ``core.bare = true`` into the
shared config, breaking the main checkout and every worktree at once.
"""

import os
import subprocess
from pathlib import Path

import pytest

from synthorg.tools._git_base import _GIT_DISCOVERY_VARS

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("var", sorted(_GIT_DISCOVERY_VARS))
def test_discovery_var_is_absent_during_tests(var: str) -> None:
    """The scrub must clear every discovery var, not just GIT_DIR."""
    assert var not in os.environ


def test_git_resolves_by_cwd_not_by_inherited_env(tmp_path: Path) -> None:
    """A git call in a tmp dir must not find the repository running the tests.

    This is the property the scrub exists to guarantee: were ``GIT_DIR`` still
    set, this would report the real checkout's root rather than failing.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not a git repository" in result.stderr.lower()


def test_bare_init_in_a_tmp_dir_cannot_reach_another_repo(tmp_path: Path) -> None:
    """``git init --bare`` must affect only its own target directory.

    Reproduces the exact corruption shape: a victim repo, and a bare init run
    elsewhere. With the discovery vars scrubbed the victim is untouched.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", str(victim)],  # noqa: S607
        capture_output=True,
        check=True,
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-q", str(elsewhere)],  # noqa: S607
        cwd=elsewhere,
        capture_output=True,
        check=True,
    )

    bare = subprocess.run(  # noqa: S603
        ["git", "-C", str(victim), "config", "--get", "core.bare"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    assert bare.stdout.strip() == "false"
