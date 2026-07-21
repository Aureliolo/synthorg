"""Pure edit planning and atomic write for the edit-file tool.

Separated from ``edit_file.py`` so the tool module stays within its size
budget: this module owns the in-memory hunk planner (uniqueness guard,
``replace_all``, no-op skipping, ordered application) and the temp-file +
replace atomic write, with no dependency on the tool class or its I/O
wrappers.
"""

import os
import pathlib
import stat
import tempfile
from pathlib import Path
from typing import Final, NamedTuple

from synthorg.tools.file_system._args import EditHunk

_ERROR_NOT_FOUND: Final[str] = "not_found"
_ERROR_NOT_UNIQUE: Final[str] = "not_unique"


class _EditPlan(NamedTuple):
    """Outcome of planning an edit without writing.

    ``resulting`` equals ``original`` whenever nothing was (or could be)
    changed, so the write step is skipped for a no-op or a rejected plan.
    ``edits_applied`` counts only the hunks that actually changed the file
    (no-op hunks whose ``old_text`` equals ``new_text`` are skipped), so the
    applied-edit report never over-counts. On rejection ``error_kind`` names
    the failure and ``error_hunk_index`` /``error_count`` locate it for the
    operator-facing message.
    """

    original: str
    resulting: str
    occurrences_found: int
    occurrences_replaced: int
    error_kind: str | None = None
    error_hunk_index: int | None = None
    error_count: int = 0
    edits_applied: int = 0


def _plan_edits_sync(resolved: Path, hunks: tuple[EditHunk, ...]) -> _EditPlan:
    """Read the file and compute the post-edit content for a hunk sequence.

    Hunks are applied in order to an in-memory copy, so a later hunk sees the
    result of the earlier ones. A hunk whose ``old_text`` is absent, or is
    non-unique without ``replace_all``, rejects the whole plan (``resulting``
    is returned equal to ``original`` so nothing is written). A hunk whose
    ``old_text`` equals its ``new_text`` is a no-op and is skipped without a
    uniqueness check.

    Args:
        resolved: Resolved file path within the workspace.
        hunks: Ordered edit hunks to apply atomically.

    Returns:
        An :class:`_EditPlan` carrying the original and resulting content,
        occurrence counts, and (on rejection) the failure descriptor.

    Raises:
        UnicodeDecodeError: If the file contains non-UTF-8 bytes.
        FileNotFoundError: If the file does not exist.
        PermissionError: If the process lacks read/write permission.
        OSError: For other OS-level I/O failures.
    """
    original = resolved.read_text(encoding="utf-8")
    working = original
    found_total = 0
    replaced_total = 0
    applied_total = 0
    for index, hunk in enumerate(hunks):
        if hunk.old_text == hunk.new_text:
            continue
        count = working.count(hunk.old_text)
        if count == 0:
            return _EditPlan(original, original, 0, 0, _ERROR_NOT_FOUND, index)
        if count > 1 and not hunk.replace_all:
            return _EditPlan(original, original, 0, 0, _ERROR_NOT_UNIQUE, index, count)
        found_total += count
        applied_total += 1
        if hunk.replace_all:
            working = working.replace(hunk.old_text, hunk.new_text)
            replaced_total += count
        else:
            working = working.replace(hunk.old_text, hunk.new_text, 1)
            replaced_total += 1
    return _EditPlan(
        original, working, found_total, replaced_total, edits_applied=applied_total
    )


def _write_sync(resolved: Path, new_content: str) -> None:
    """Write *new_content* atomically (temp file + replace), preserving mode.

    The atomic pattern ensures a crash or disk-full during the write does not
    corrupt the original file. ``mkstemp`` creates the temporary file
    owner-only, so its permission bits are set to the target's before the
    replace: editing an executable or group-readable file must not silently
    narrow its mode. The mode is applied via ``os.fchmod`` on the still-open
    descriptor so no window exists in which the temp *path* could be swapped
    for a symlink in a writable parent directory; only where ``fchmod`` is
    absent (Windows) is a path chmod used, and there ``chmod`` merely toggles
    the read-only bit so the race is not meaningful.

    Raises:
        OSError: For OS-level I/O failures.
        BaseException: Re-raised after unlinking the temp file on any failure.
    """
    mode = stat.S_IMODE(resolved.stat().st_mode)
    fchmod = getattr(os, "fchmod", None)
    fd, tmp_path = tempfile.mkstemp(dir=str(resolved.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
            fh.flush()
            os.fsync(fh.fileno())
            if fchmod is not None:
                fchmod(fh.fileno(), mode)
        if fchmod is None:
            Path(tmp_path).chmod(mode)
        pathlib.Path(tmp_path).replace(resolved)
    except BaseException:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise
