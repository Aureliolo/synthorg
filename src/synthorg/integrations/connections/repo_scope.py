"""Shape validation for a forge connection's ``allowed_repos`` scope.

A scope entry is ``owner/repo`` (or an ``owner/*`` glob). The check is
shared by the ``Connection`` entity and the API request models so the
least-privilege invariant holds wherever a scope is written: a bare ``*``
or a partial glob would over-match ``fnmatch`` and silently admit every
repository, and a no-slash / multi-slash entry is not a valid identifier.
"""

from typing import Final

# Characters that must never appear in a repo-scope segment.
_SCOPE_FORBIDDEN: Final[frozenset[str]] = frozenset({"\\", "?", "#", "@", "%", " "})
_SCOPE_CONTROL_THRESHOLD: Final[int] = 0x20
# A scope entry is exactly ``owner/repo`` (owner + repo = two segments).
_SCOPE_ENTRY_SEGMENTS: Final[int] = 2


def _validate_scope_segment(segment: str, *, field: str, allow_glob: bool) -> None:
    """Validate one owner/repo segment of a repo-scope entry.

    Raises:
        ValueError: When the segment is empty, an over-broad glob, or
            carries a traversal / separator / control character.
    """
    if not segment:
        msg = f"repo scope entry has an empty {field}"
        raise ValueError(msg)
    if segment == "*":
        if allow_glob:
            return
        msg = f"repo scope entry {field} must be a concrete name, not '*'"
        raise ValueError(msg)
    if "*" in segment:
        msg = f"repo scope entry {field} must be a full name or '*', not a partial glob"
        raise ValueError(msg)
    if ".." in segment or any(ch in segment for ch in _SCOPE_FORBIDDEN):
        msg = f"repo scope entry {field} contains a disallowed character"
        raise ValueError(msg)
    if any(ord(ch) < _SCOPE_CONTROL_THRESHOLD for ch in segment):
        msg = f"repo scope entry {field} contains a control character"
        raise ValueError(msg)


def validate_repo_scope_entry(entry: str) -> None:
    """Validate one ``allowed_repos`` scope entry (``owner/repo`` / ``owner/*``).

    The owner must be a concrete identifier and the repo a concrete
    identifier or a bare ``*`` glob. A no-slash entry, a multi-slash entry,
    or a ``*`` on the owner side would either be a dead no-op or match every
    repository, so both are rejected before they reach the ``fnmatch`` scope
    check (which is fail-closed on an empty scope but permissive on a `*`).

    Raises:
        ValueError: When the entry is not exactly ``owner/repo`` with a
            concrete owner and a concrete-or-``*`` repo.
    """
    parts = entry.split("/")
    if len(parts) != _SCOPE_ENTRY_SEGMENTS:
        msg = f"repo scope entry {entry!r} must be exactly 'owner/repo' or 'owner/*'"
        raise ValueError(msg)
    _validate_scope_segment(parts[0], field="owner", allow_glob=False)
    _validate_scope_segment(parts[1], field="repo", allow_glob=True)


__all__ = ["validate_repo_scope_entry"]
