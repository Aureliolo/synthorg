# module-kind: code
"""Get a run's report, or refuse it and say why.

Kept apart from the classification the report feeds, because this half answers
a different question against a different threat model. What a case OUTCOME
means is a reading of trusted structure; whether these bytes may be read at all
is a decision about a path and a file inside a directory an agent writes, and
every refusal here is a security boundary rather than a verdict.

The manifest names the path and the agent commits the manifest, so both the
path and the bytes it names are untrusted input even though the manifest is the
authority on what is pending. A refusal is never an outage: it classifies every
pending criterion red, which is a rework round, against believing a report
nobody could vouch for.
"""

import os
import stat
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Final
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import parse as parse_xml

from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_PENDING_REPORT_ESCAPED,
    ENVIRONMENT_PENDING_REPORT_UNREADABLE,
)

logger = get_logger(__name__)

#: Ceiling on a report the parser will build a tree from, enforced by the READ
#: rather than by a size measured before it: a size read off the descriptor
#: describes the file at that instant and binds nothing the parser goes on to
#: pull from it, so a file appended to in between would be parsed whole. One
#: owner, and it is the one that cannot be overtaken. A report is one run's
#: per-test results, so a real one is orders of magnitude under this; the limit
#: exists because the file is written inside a workspace an agent controls and
#: the parse happens on the API's own event loop, where an unbounded tree is a
#: memory ceiling somebody else picks.
_MAX_REPORT_BYTES: Final[int] = 32 * 1024 * 1024

#: Flags the report is opened with, so the descriptor validated below is the
#: one parsed. ``O_NONBLOCK`` keeps a FIFO from blocking the open itself (the
#: refusal comes from the ``fstat`` after it), ``O_NOFOLLOW`` refuses a symlink
#: swapped in for the report itself, and ``O_BINARY`` keeps Windows from
#: translating line endings under the parser. Each is absent on the platforms
#: that do not have it, where zero leaves the flag set unchanged.
_REPORT_OPEN_FLAGS: Final[int] = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)  # lint-allow: ghost-attribute-read -- POSIX-only
    | getattr(os, "O_NOFOLLOW", 0)  # lint-allow: ghost-attribute-read -- POSIX-only
    | getattr(os, "O_BINARY", 0)  # lint-allow: ghost-attribute-read -- Windows-only
)

#: Flags each directory on the way down is opened with. ``O_NOFOLLOW`` applies
#: to the LAST component of whatever it opens, so opening the report by its
#: full path leaves every directory above it free to be a symlink; walking the
#: components puts each one in that last position in turn, which is the only
#: way each gets checked.
_DIRECTORY_OPEN_FLAGS: Final[int] = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)  # lint-allow: ghost-attribute-read -- POSIX-only
    | getattr(os, "O_NOFOLLOW", 0)  # lint-allow: ghost-attribute-read -- POSIX-only
    | getattr(os, "O_NONBLOCK", 0)  # lint-allow: ghost-attribute-read -- POSIX-only
)

#: Whether this platform can open a name relative to a directory descriptor.
#: Windows cannot, and there the walk degrades to opening the resolved path:
#: the descent is the whole mechanism, so there is nothing partial to keep.
#: The workspace an agent writes to is a container on Linux, which is where
#: the threat this closes lives; a developer platform that cannot express the
#: walk would otherwise be unable to read a report at all.
_SUPPORTS_DIR_FD: Final[bool] = os.open in os.supports_dir_fd


def read_report(
    workspace_path: Path,
    test_report_path: str | None,
    *,
    not_before: datetime | None,
) -> Element | None:
    """Read and parse the run's report.

    A path escaping the workspace is refused rather than followed, and the parse
    is entity-hardened: an expanded external entity here would read the
    backend's filesystem on the agent's behalf.

    Three things about the file are checked before its contents are believed.
    It must be a regular file: a named pipe passes every path check and then
    blocks the parse for ever, which on this call path is the whole event loop.
    It must be at least as new as the run it is offered as evidence about,
    because one report path is shared by every unit in the project and nothing
    rewrites it when a run dies before producing one. And it must be small
    enough to hold: the parser builds a tree, so a report the size of a disk is
    a memory ceiling an agent chooses.

    Returns:
        The parsed root element, or ``None`` when the report is absent, outside
        the workspace, not a regular file, too large, older than the run, or
        unparseable.
    """
    if test_report_path is None:
        # Declared pending criteria with no report to classify them from is
        # refused at the model, so reaching this means a project declared
        # pending entries some other way. Logged rather than returned in
        # silence, because it lands every criterion red and the operator would
        # otherwise see the verdict with nothing naming its cause.
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=None,
            reason="no_report_declared",
        )
        return None
    root = workspace_path.resolve()
    resolved = (root / test_report_path).resolve()
    if not resolved.is_relative_to(root):
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_ESCAPED,
            report_path=test_report_path,
        )
        return None
    try:
        # Opened ONCE, and every check below asks the descriptor rather than
        # the name. Validating the path and then handing the name to the parser
        # is two lookups of a path inside a directory the agent writes, and the
        # gap between them is enough to swap a validated regular file for a
        # FIFO (which blocks this parse, on the API's own event loop) or for a
        # symlink out of the workspace.
        with os.fdopen(_open_within(root, resolved), "rb") as handle:
            if not _is_usable_evidence(handle, test_report_path, not_before):
                return None
            raw = handle.read(_MAX_REPORT_BYTES + 1)
        if len(raw) > _MAX_REPORT_BYTES:
            logger.warning(
                ENVIRONMENT_PENDING_REPORT_UNREADABLE,
                report_path=test_report_path,
                reason="report_too_large",
            )
            return None
        # Parsed from the bytes already taken rather than from the live
        # descriptor, so what the tree is built from is exactly what the
        # ceiling above was applied to.
        return parse_xml(BytesIO(raw)).getroot()
    except (OSError, ValueError, ParseError, DefusedXmlException) as exc:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=test_report_path,
            error_type=type(exc).__name__,
        )
        return None


def _open_within(root: Path, resolved: Path) -> int:
    """Open *resolved* by descending from *root* one component at a time.

    ``resolved`` was produced by :py:meth:`~pathlib.Path.resolve` and checked
    against ``root``, but that check describes the tree as it was: the agent
    writes inside this workspace, so it can replace an intermediate directory
    with a symlink out of it before the open. ``O_NOFOLLOW`` alone does not
    cover that, because it constrains only the final component of the name it
    is given. Descending puts every component in that final position in turn.

    Returns:
        A descriptor for the report, opened relative to a directory chain that
        contained no symlink.

    Raises:
        OSError: When any component is a symlink, is not a directory, or
            cannot be opened. Handled by the caller alongside every other way
            the report refuses to be read.
        ValueError: When the path names the workspace root itself, which is a
            directory rather than a report.
    """
    parts = resolved.relative_to(root).parts
    if not parts:
        msg = "the declared report path names the workspace root, not a file"
        raise ValueError(msg)
    if not _SUPPORTS_DIR_FD:
        return os.open(resolved, _REPORT_OPEN_FLAGS)
    directory = os.open(root, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in parts[:-1]:
            nested = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = nested
        return os.open(parts[-1], _REPORT_OPEN_FLAGS, dir_fd=directory)
    finally:
        os.close(directory)


def _is_usable_evidence(
    handle: BinaryIO,
    declared: str,
    not_before: datetime | None,
) -> bool:
    """Whether the open file *handle* can stand as evidence about this run.

    Asks the descriptor rather than the path, so what is measured here is what
    the parser goes on to read: nothing can be substituted in between.

    Size is deliberately not asked here: the answer would describe the file
    before the read rather than bound it, and the read enforces the ceiling
    itself.

    Returns:
        Whether it is a regular file and no older than the run being judged.

    Raises:
        OSError: Propagated from the stat, and handled by the caller alongside
            every other way the file can refuse to be read.
    """
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode):
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=declared,
            reason="not_a_regular_file",
        )
        return False
    if not_before is None:
        return True
    written = datetime.fromtimestamp(info.st_mtime, tz=UTC)
    if written < not_before:
        logger.warning(
            ENVIRONMENT_PENDING_REPORT_UNREADABLE,
            report_path=declared,
            reason="report_predates_the_run",
        )
        return False
    return True


__all__ = ["read_report"]
