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
    # The non-zero return code is the property: a leaked GIT_DIR would make
    # this resolve the real checkout and exit 0. stdout carries the resolved
    # toplevel on success, so its emptiness positively confirms nothing
    # resolved, without depending on git's (localisable) stderr wording.
    assert result.returncode != 0
    assert not result.stdout.strip()


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


# The bare-repo guard in ``scripts/git-hooks/_run-hook.sh`` fires on exactly
# this command's output. The tests below pin its contract directly rather than
# invoking the bash script, which cannot run portably from pytest on Windows
# (``bash`` resolves to WSL, which mangles the Windows ``cwd``): the guard's
# whole behaviour reduces to this one git call, so testing it here is both
# faithful and deterministic.
_HOOK_BARE_PROBE = ("git", "config", "--local", "--bool", "--get", "core.bare")

# Every spelling git accepts as a true boolean. The guard normalises with
# ``--bool`` so all of them, not just the literal ``true``, are caught.
_TRUTHY_BARE_SPELLINGS = ("true", "yes", "on", "1")


def _isolated_git_env(global_config: Path | None = None) -> dict[str, str]:
    """A git env with system config off and global config pinned or empty.

    Keeps the probe from reading the developer's real ``~/.gitconfig`` (or the
    machine's system config), so the only ``core.bare`` in play is the one the
    test sets.
    """
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(global_config) if global_config else os.devnull
    return env


def _init_repo(path: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", str(path)],  # noqa: S607
        capture_output=True,
        check=True,
    )


@pytest.mark.parametrize("spelling", _TRUTHY_BARE_SPELLINGS)
def test_hook_guard_detects_every_truthy_local_bare_spelling(
    tmp_path: Path, spelling: str
) -> None:
    """A local ``core.bare`` set to any truthy spelling reads back as ``true``.

    A literal ``= "true"`` match would miss ``yes`` / ``on`` / ``1``; ``--bool``
    canonicalises them, so the guard blocks a bare repo however it was written.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "config", "--local", "core.bare", spelling],  # noqa: S607
        capture_output=True,
        check=True,
    )

    probe = subprocess.run(  # noqa: S603
        _HOOK_BARE_PROBE,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_isolated_git_env(),
    )
    assert probe.stdout.strip() == "true"


def test_hook_guard_ignores_a_global_only_bare_setting(tmp_path: Path) -> None:
    """A global ``core.bare=true`` must not make the guard block a normal repo.

    ``--local`` scopes the probe to the repo's own config, which is where the
    inherited-``GIT_DIR`` corruption lands. A stray global setting (someone's
    ``~/.gitconfig``) has no work-tree consequence for a real checkout and must
    not trip the guard.
    """
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[core]\n\tbare = true\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # ``git init`` writes an explicit local ``core.bare = false``. Remove it so
    # the ONLY ``core.bare`` anywhere is the global ``true``; otherwise the
    # local ``false`` would shadow it and the test would prove nothing about
    # scope.
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "config", "--local", "--unset", "core.bare"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    env = _isolated_git_env(global_config)

    local_probe = subprocess.run(  # noqa: S603
        _HOOK_BARE_PROBE,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    # The guard sees nothing local, so it does not fire.
    assert local_probe.stdout.strip() == ""

    # Sanity: the global value IS visible across all scopes, so the empty
    # local read above is the ``--local`` scoping at work, not a config that
    # simply failed to load.
    all_scopes = subprocess.run(
        ["git", "config", "--bool", "--get", "core.bare"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert all_scopes.stdout.strip() == "true"
