"""Shared fixtures for the ask-policy unit tests.

The ask-policy provider is a process-global ambient singleton (set at boot and
on a settings change), so a test that binds one must not leak it into a sibling
asserting the unbound default.

Teardown restores whatever was bound *before* the test rather than forcing
``None``: under the session-scoped app fixture in ``tests/unit/api`` a real
provider may be bound on the same xdist worker, and forcing ``None`` would stomp
it for every later test on that worker.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.ask_policy.provider import (
    current_ask_policy_provider,
    set_ask_policy_provider,
)
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue
from synthorg.settings.service import SettingsService
from tests._shared import mock_of


@pytest.fixture(autouse=True)
def _reset_ask_policy_ambient() -> Iterator[None]:
    previous = current_ask_policy_provider()
    set_ask_policy_provider(None)
    try:
        yield
    finally:
        set_ask_policy_provider(previous)


def agent(*, role: str = "Developer", department: str = "Engineering") -> AgentIdentity:
    """Build the agent every ask-policy test renders a prompt for.

    Returns:
        A minimal identity whose role and department drive scope filtering.
    """
    return AgentIdentity(
        name="Test Agent",
        role=role,
        department=department,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        personality=PersonalityConfig(description="A precise thinker."),
    )


def settings_service(**values: str) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any by design
    """Build a settings service resolving the ``engine`` ask-policy keys.

    An autospec'd double returning REAL ``SettingValue`` objects rather than a
    hand-rolled class behind a cast: the cast satisfies the type checker while
    typeguard rejects it at the runtime boundary, and a stand-in that cannot
    produce the contract's own return type proves nothing about the caller.

    Returns:
        The double, resolving each supplied key and defaulting the rest.
        ``Any`` matches ``mock_of``'s deliberate static signature, so call
        sites need no cast.
    """
    resolved = {
        "ask_policy_enabled": "true",
        "ask_policy_extra_directives": "[]",
        **values,
    }

    async def _get(namespace: str, key: str) -> SettingValue:
        assert namespace == SettingNamespace.ENGINE
        return SettingValue(
            namespace=SettingNamespace.ENGINE,
            key=NotBlankStr(key),
            value=resolved[key],
            source=SettingSource.DEFAULT,
        )

    return mock_of[SettingsService](get=AsyncMock(side_effect=_get))


def failing_settings_service() -> Any:  # type: ignore[explicit-any]  # mock_of returns Any by design
    """Build a settings service whose reads fail for a recoverable reason.

    Returns:
        The double, raising ``OSError`` on every read.
    """
    return mock_of[SettingsService](get=AsyncMock(side_effect=OSError("backend down")))
