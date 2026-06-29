"""Tests for the security-weakening settings write guardrail.

Disabling a security toggle (or switching the output-scan policy to the
permissive ``log_only``) must require a deliberate confirm + reason + actor;
enabling / tightening must apply with no gate.
"""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
)

pytestmark = pytest.mark.unit


def _current_factory(
    values: dict[tuple[str, str], str | None],
) -> Callable[[str, str], Awaitable[str | None]]:
    """Build a ``get_current`` coroutine returning *values* by (ns, key)."""

    async def _get_current(namespace: str, key: str) -> str | None:
        return values.get((namespace, key))

    return _get_current


_SATISFIED = SettingsWriteGovernance(confirm=True, reason="incident", actor="ceo")


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "true", "false"),
        ("audit_enabled", "true", "false"),
        ("post_tool_scanning_enabled", "true", "false"),
        ("output_scan_policy_type", "autonomy_tiered", "log_only"),
    ],
)
async def test_weakening_without_confirmation_rejected(
    key: str, current: str, new: str
) -> None:
    """Each weakening transition raises without a satisfied governance."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", key, new)],
            governance=None,
            get_current=_current_factory({("security", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "true", "false"),
        ("output_scan_policy_type", "redact", "log_only"),
    ],
)
async def test_weakening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    """A satisfied governance authorises the weakening transition."""
    await enforce_security_write_governance(
        [("security", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("security", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "false", "true"),
        ("audit_enabled", "false", "true"),
        ("output_scan_policy_type", "log_only", "withhold"),
        ("output_scan_policy_type", "autonomy_tiered", "redact"),
    ],
)
async def test_enabling_or_tightening_is_unguarded(
    key: str, current: str, new: str
) -> None:
    """Enable / tighten transitions never consult governance."""
    await enforce_security_write_governance(
        [("security", key, new)],
        governance=None,
        get_current=_current_factory({("security", key): current}),
    )


@pytest.mark.parametrize(
    ("confirm", "reason", "actor"),
    [
        (False, "incident", "ceo"),
        (True, "", "ceo"),
        (True, "incident", ""),
        (True, "   ", "ceo"),
    ],
)
async def test_incomplete_governance_is_not_satisfied(
    confirm: bool, reason: str, actor: str
) -> None:
    """confirm + non-blank reason + non-blank actor are all required."""
    governance = SettingsWriteGovernance(confirm=confirm, reason=reason, actor=actor)
    assert governance.is_satisfied is False
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", "enabled", "false")],
            governance=governance,
            get_current=_current_factory({("security", "enabled"): "true"}),
        )


async def test_first_write_of_false_is_weakening() -> None:
    """An unset toggle defaults to 'true', so a first write of false is guarded."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", "enabled", "false")],
            governance=None,
            get_current=_current_factory({}),
        )


async def test_non_security_namespace_is_ignored() -> None:
    """Non-security keys never consult the guardrail."""
    await enforce_security_write_governance(
        [("api", "enabled", "false")],
        governance=None,
        get_current=_current_factory({}),
    )
