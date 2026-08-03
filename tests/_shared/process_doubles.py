"""Typed stand-ins for the process objects the hook scripts drive.

``subprocess.Popen`` and ``subprocess.CompletedProcess`` set ``pid`` /
``returncode`` / ``stdout`` / ``stderr`` in ``__init__`` rather than on the
class, so ``create_autospec`` cannot see them and rung 2 of the test-double
ladder does not reach these seams. These are rung 1: hand-written fakes
naming exactly the contract the scripts read, so a script that starts
reading a fifth attribute fails here instead of silently accepting whatever
an attribute bag hands back.
"""

from dataclasses import dataclass

__all__ = ["FakeCommandResult", "FakeProcess"]


@dataclass(frozen=True, slots=True)
class FakeProcess:
    """A spawned process, as a kill helper sees one."""

    pid: int


@dataclass(frozen=True, slots=True)
class FakeCommandResult:
    """A finished command, as ``subprocess.run`` reports one."""

    returncode: int
    stdout: str = ""
    stderr: str = ""
