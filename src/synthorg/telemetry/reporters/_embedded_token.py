"""Embedded write-only telemetry-backend project token.

Sentinel value lives in source. The release workflow rewrites this
file in-place before ``uv build`` so the published wheel carries
the real write-only project token; local source installs and
non-release builds keep the sentinel and fall back to disabled
(plus a single-shot ERROR if telemetry was explicitly enabled).

Operators do NOT configure this token. They flip
``SYNTHORG_TELEMETRY_ENABLED=1`` (or the equivalent
``telemetry.enabled`` setting) and either get the embedded token
(release wheel) or a startup ERROR explaining the build artifact
is missing it.

Build pipeline reference: ``scripts/embed_logfire_token.py`` and
``.github/workflows/release.yml``.
"""

from typing import Final

# Sentinel string is detectable at runtime via ``is_token_embedded()``.
# DO NOT change the sentinel without also updating the embedder script
# AND the comparison in ``is_token_embedded``.
_TOKEN_SENTINEL: Final[str] = "__SYNTHORG_TELEMETRY_TOKEN_NOT_EMBEDDED__"  # noqa: S105

EMBEDDED_TELEMETRY_TOKEN: Final[str] = _TOKEN_SENTINEL


def is_token_embedded() -> bool:
    """Return True when the build pipeline embedded a real token.

    A bare ``EMBEDDED_TELEMETRY_TOKEN != _TOKEN_SENTINEL`` check would
    work equally well, but routing through this helper gives the
    collector and the factory a single import and keeps the
    sentinel comparison logic in one place.
    """
    return EMBEDDED_TELEMETRY_TOKEN != _TOKEN_SENTINEL
