"""Unit tests for the credentialed-MCP controller's pure helpers.

The Request-taking body reader (``_read_messages``) is exercised in the
integration tier; here we cover the disabled-server guard and the capability
grant parsing, which decide whether the endpoint serves at all and what an
actor may see.
"""

import pytest

from synthorg.api.mcp_gateway.controller import _parse_capabilities, _require_enabled
from synthorg.core.domain_errors import ServiceUnavailableError

pytestmark = pytest.mark.unit


def test_require_enabled_raises_when_disabled() -> None:
    with pytest.raises(ServiceUnavailableError):
        _require_enabled(enabled=False)


def test_require_enabled_passes_when_enabled() -> None:
    _require_enabled(enabled=True)  # no raise


def test_parse_capabilities_splits_and_strips() -> None:
    assert _parse_capabilities("forge:read, chat:write ,connections:*") == (
        "forge:read",
        "chat:write",
        "connections:*",
    )


def test_parse_capabilities_drops_blank_entries() -> None:
    assert _parse_capabilities("forge:read,, ,chat:read") == (
        "forge:read",
        "chat:read",
    )


def test_parse_capabilities_empty_grant_is_empty_tuple() -> None:
    assert _parse_capabilities("") == ()
    assert _parse_capabilities("   ") == ()
