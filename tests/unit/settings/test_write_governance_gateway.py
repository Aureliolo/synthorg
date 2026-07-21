"""Governance tests for the ``providers.gateway_enabled`` weakening guard."""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit

_ENABLE = ("providers", "gateway_enabled", "true")
_DISABLE = ("providers", "gateway_enabled", "false")
_SATISFIED = SettingsWriteGovernance(confirm=True, reason="ops", actor="admin")


def _current(value: str | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def _get(_namespace: str, _key: str) -> str | None:
        return value

    return _get


async def test_enabling_gateway_without_governance_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [_ENABLE], governance=None, get_current=_current("false")
        )


async def test_enabling_gateway_from_unset_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [_ENABLE], governance=None, get_current=_current(None)
        )


async def test_enabling_gateway_with_governance_is_allowed() -> None:
    await enforce_security_write_governance(
        [_ENABLE], governance=_SATISFIED, get_current=_current("false")
    )


async def test_disabling_gateway_is_unguarded() -> None:
    await enforce_security_write_governance(
        [_DISABLE], governance=None, get_current=_current("true")
    )


_MCP_ENABLE = ("tools", "credentialed_mcp_enabled", "true")
_MCP_DISABLE = ("tools", "credentialed_mcp_enabled", "false")


async def test_enabling_credentialed_mcp_without_governance_is_rejected() -> None:
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [_MCP_ENABLE], governance=None, get_current=_current("false")
        )


async def test_enabling_credentialed_mcp_with_governance_is_allowed() -> None:
    await enforce_security_write_governance(
        [_MCP_ENABLE], governance=_SATISFIED, get_current=_current("false")
    )


async def test_disabling_credentialed_mcp_is_unguarded() -> None:
    await enforce_security_write_governance(
        [_MCP_DISABLE], governance=None, get_current=_current("true")
    )
