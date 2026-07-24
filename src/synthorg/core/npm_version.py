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
_EXACT_NPM_VERSION: Final[re.Pattern[str]] = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def is_exact_npm_version(version: str) -> bool:
    """Whether *version* pins one immutable published release.

    Returns:
        ``True`` for an exact semver version, ``False`` for a dist-tag, a
        range, a partial version, a wildcard, or an empty string.
    """
    return bool(_EXACT_NPM_VERSION.match(version))


__all__ = ["is_exact_npm_version"]
