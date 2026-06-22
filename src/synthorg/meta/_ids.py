# module-kind: code
"""Shared opaque-identifier minting for meta-layer services.

Single source of truth for the ``NotBlankStr(str(uuid.uuid4()))`` idiom
that charter, conversational-propose parking, and group-invite services
each reached for when stamping a fresh approval / charter id.
"""

import uuid

from synthorg.core.types import NotBlankStr


def new_opaque_id() -> NotBlankStr:
    """Return a fresh opaque identifier as a non-blank string.

    Returns:
        A UUID4 rendered as ``NotBlankStr``.
    """
    return NotBlankStr(str(uuid.uuid4()))
