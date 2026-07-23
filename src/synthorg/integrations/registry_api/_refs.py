"""Validation for OCI image references (digest / tag / repository).

One home for the reference grammar so the agent-facing argument models and
the registry client validate the same shapes. A reference interpolated into
a request path is the seam where the egress pin stops being structural, so
each predicate is strict: an image digest is content-addressed, a tag matches
the OCI tag grammar, and a repository is a slash-joined chain of OCI path
components.
"""

import re
from typing import Final

# ``algorithm:hex`` with the algorithm and hex-digest lengths the OCI image
# spec mandates. sha256 (64) and sha512 (128) are the registered algorithms;
# the pattern accepts either so a stronger digest is not rejected.
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(
    r"^sha(?:256:[a-f0-9]{64}|512:[a-f0-9]{128})$"
)
# One OCI tag: a leading word char then up to 127 of word / period / dash.
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}$")
# One repository path component (lowercase, OCI ``pathcomponent`` grammar).
_REPO_COMPONENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*$"
)
_MAX_REPOSITORY_LEN: Final[int] = 255


def valid_digest(value: str) -> bool:
    """Return whether *value* is a well-formed content-addressable digest."""
    return bool(_DIGEST_RE.match(value))


def valid_tag(value: str) -> bool:
    """Return whether *value* is a well-formed OCI tag."""
    return bool(_TAG_RE.match(value))


def valid_reference(value: str) -> bool:
    """Return whether *value* is a usable manifest reference (tag or digest)."""
    return valid_tag(value) or valid_digest(value)


def valid_repository(value: str) -> bool:
    """Return whether *value* is a well-formed OCI repository path.

    A repository is one or more ``pathcomponent`` values joined by ``/``. It
    is bounded in length and rejects an empty component so a stray leading,
    trailing or doubled slash cannot rewrite the request path.
    """
    if not value or len(value) > _MAX_REPOSITORY_LEN:
        return False
    return all(_REPO_COMPONENT_RE.match(part) for part in value.split("/"))


__all__ = [
    "valid_digest",
    "valid_reference",
    "valid_repository",
    "valid_tag",
]
