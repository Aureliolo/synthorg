"""What counts as an immutable npm version pin.

Two independent paths launch an npm package under the agent's tools: a
curated ``CatalogEntry`` (whose ``npm_version`` the installer renders into
``npx -y <pkg>@<version>``) and a hand-authored ``MCPServerConfig`` (whose
argv names the spec directly). Both must reject anything that re-resolves
at spawn time, so the rule lives here once rather than as two copies that
can drift apart into disagreeing definitions of "pinned".
"""

import re
from typing import Final

# The only npm version form that names one immutable published artifact:
# MAJOR.MINOR.PATCH with optional pre-release and/or build metadata. Every
# other selector floats to whatever is newest that satisfies it -- a
# dist-tag (``latest``/``next``), a range (``^1.2.3``/``~1.2.3``/``>=1``),
# a partial version (``1``/``1.2``), or a wildcard (``1.x``/``*``) -- which
# defeats the pin, so the check allowlists this shape instead of trying to
# enumerate the floating ones.
#
# The grammar is the SemVer 2.0.0 one, spelled strictly rather than
# loosely: ``[0-9]`` rather than ``\d`` (which also matches Unicode digits
# such as U+FF11, a spec-invalid version npm would never resolve), no
# leading zeros on a numeric component or numeric pre-release identifier,
# and no empty dot-separated segment. A malformed spec is not a pin, and a
# pin check that accepts one is a pin check that can be talked past.
_NUM: Final[str] = r"0|[1-9][0-9]*"
_PRERELEASE_ID: Final[str] = rf"(?:{_NUM}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_ID: Final[str] = r"[0-9A-Za-z-]+"
_EXACT_NPM_VERSION: Final[re.Pattern[str]] = re.compile(
    rf"(?:{_NUM})\.(?:{_NUM})\.(?:{_NUM})"
    rf"(?:-{_PRERELEASE_ID}(?:\.{_PRERELEASE_ID})*)?"
    rf"(?:\+{_BUILD_ID}(?:\.{_BUILD_ID})*)?"
)


def is_exact_npm_version(version: str) -> bool:
    """Whether *version* pins one immutable published release.

    Matched with :meth:`re.Pattern.fullmatch`, not ``match``: ``$`` also
    matches before a trailing newline, so a version with one appended
    would otherwise read as pinned.

    Returns:
        ``True`` for an exact semver version, ``False`` for a dist-tag, a
        range, a partial version, a wildcard, a malformed version, or an
        empty string.
    """
    return bool(_EXACT_NPM_VERSION.fullmatch(version))


__all__ = ["is_exact_npm_version"]
