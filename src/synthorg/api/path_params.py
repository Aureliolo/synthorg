"""Constrained path parameter types for API controllers.

Provides ``Annotated`` type aliases with ``max_length`` and
``min_length`` constraints, applied at the framework level
by Litestar's ``PathParameter`` metadata.  Follows the same
pattern as ``pagination.py`` for query parameter types.
"""

from typing import Annotated

from litestar.params import PathParameter

from synthorg.core.types import NotBlankStr

PathId = Annotated[
    str,
    PathParameter(max_length=128, min_length=1, description="Resource identifier"),
]
"""Path parameter type for resource identifiers (1-128 chars)."""

PathName = Annotated[
    str,
    PathParameter(max_length=128, min_length=1, description="Resource name"),
]
"""Path parameter type for resource names (1-128 chars)."""

PathNamespace = Annotated[
    str,
    PathParameter(max_length=64, min_length=1, description="Settings namespace"),
]
"""Path parameter type for settings namespaces (1-64 chars)."""

PathKey = Annotated[
    str,
    PathParameter(max_length=128, min_length=1, description="Settings key"),
]
"""Path parameter type for settings keys (1-128 chars)."""

PathField = Annotated[
    NotBlankStr,
    PathParameter(max_length=128, min_length=1, description="Credential field name"),
]
"""Path parameter type for credential / secret field names (1-128 chars).

``NotBlankStr`` rejects whitespace-only values in addition to the
length bound -- a path segment of ``"   "`` is identifier-shaped on
the wire but semantically blank, so refusing it at the boundary
keeps audit logs and downstream lookups from carrying meaningless
identifiers.
"""

PathEventType = Annotated[
    NotBlankStr,
    PathParameter(max_length=64, min_length=1, description="Webhook event type"),
]
"""Path parameter type for webhook event-type identifiers (1-64 chars).

``NotBlankStr`` rejects whitespace-only values; same rationale as
:data:`PathField` above.
"""

# Max lengths for query parameter validation (shared with inline checks
# where Litestar does not enforce Parameter constraints on optional params).
QUERY_MAX_LENGTH: int = 128
"""Default max length for string query filter parameters."""
