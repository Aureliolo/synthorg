"""Tests for the workspace sharing contract between backend and sandbox."""

import os
import stat

import pytest

from synthorg.core.workspace_sharing import (
    SHARED_UMASK,
    WORKSPACE_DIR_MODE,
    WORKSPACE_FILE_MODE,
    apply_shared_umask,
    delivered_file_mode,
    workspace_share_gid,
)


@pytest.mark.unit
def test_file_mode_grants_the_group_read_and_write() -> None:
    """A delivered file must be readable and writable by the shared group.

    The sandbox joins that group as a supplementary gid; group-read is what
    lets a test runner open the source, group-write is what lets a formatter
    or a build step rewrite it in place.
    """
    assert WORKSPACE_FILE_MODE & stat.S_IRGRP
    assert WORKSPACE_FILE_MODE & stat.S_IWGRP


@pytest.mark.unit
def test_nothing_is_granted_to_other() -> None:
    """The group IS the sharing mechanism, so a world bit only widens reach."""
    assert not WORKSPACE_FILE_MODE & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)
    assert not WORKSPACE_DIR_MODE & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH)


@pytest.mark.unit
def test_dir_mode_is_setgid_and_group_writable() -> None:
    """Directories carry setgid so what the sandbox creates stays shared.

    Without it a file the sandbox writes lands under its own primary gid and
    the backend, which is only a group member, cannot read the build output
    back.
    """
    assert WORKSPACE_DIR_MODE & stat.S_ISGID
    assert WORKSPACE_DIR_MODE & stat.S_IWGRP
    assert WORKSPACE_DIR_MODE & stat.S_IXGRP


@pytest.mark.unit
def test_an_executable_script_stays_executable() -> None:
    """Overwriting must never narrow what a file already grants."""
    assert delivered_file_mode(0o750) == 0o750


@pytest.mark.unit
def test_a_widened_file_is_not_narrowed_back() -> None:
    """A build may widen a file on purpose; a write is no reason to undo that."""
    assert delivered_file_mode(0o664) == 0o664


@pytest.mark.unit
def test_a_group_readable_file_is_not_granted_group_write() -> None:
    """ "Can already read" is the whole test, and it stops at read.

    The boundary the docstring promises and the one the other cases miss:
    ``0o664`` and ``0o600`` both behave correctly under a rule that widens
    whenever the group cannot WRITE, so only a file the group can read but
    not write can tell the two rules apart. A deliberately read-only file
    must survive a write untouched.
    """
    assert delivered_file_mode(0o644) == 0o644


@pytest.mark.unit
def test_an_owner_only_file_is_repaired_on_the_next_write() -> None:
    """Files written before the contract existed are ``0o600``.

    Leaving them alone would keep them invisible to the sandbox forever.
    """
    assert delivered_file_mode(0o600) == WORKSPACE_FILE_MODE


@pytest.mark.unit
def test_repairing_an_owner_only_program_keeps_its_execute_bit() -> None:
    """The group is granted what the owner holds, rather than a flat mode."""
    assert delivered_file_mode(0o700) == 0o770


@pytest.mark.unit
def test_a_new_file_takes_the_share_mode() -> None:
    """Creation states its mode rather than inheriting the process umask.

    ``tempfile.mkstemp`` creates owner-only by design and the backend's umask
    is ``022``, so neither the primitive nor the ambient default produces a
    group-writable file.
    """
    assert delivered_file_mode(None) == WORKSPACE_FILE_MODE


@pytest.mark.unit
def test_share_gid_is_the_running_process_group() -> None:
    """The gid is derived, never configured, so it cannot drift.

    A configured value would be a second owner for a fact the OS already
    holds, and a stale one silently returns the sandbox to no access at all.
    """
    getgid = getattr(os, "getgid", None)
    if getgid is None:
        assert workspace_share_gid() is None
    else:
        assert workspace_share_gid() == getgid()


@pytest.mark.unit
def test_the_shared_umask_withholds_nothing_from_the_group() -> None:
    """The lever over files this code never sees.

    ``core.sharedRepository=group`` covers the files git manages and not
    ``COMMIT_EDITMSG``, which git writes under the umask: whichever
    identity committed first left it owner-only-writable and the other's
    ``git commit`` failed for the life of the workspace.
    """
    assert SHARED_UMASK & stat.S_IRWXG == 0
    assert SHARED_UMASK & stat.S_IWOTH == stat.S_IWOTH


@pytest.mark.unit
@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX umask; Windows has no equivalent process mask.",
)
def test_applying_it_returns_the_previous_mask() -> None:
    """So a caller can restore it, and a test can leave the process as found."""
    previous = apply_shared_umask()
    try:
        assert os.umask(SHARED_UMASK) == SHARED_UMASK
    finally:
        os.umask(previous)
