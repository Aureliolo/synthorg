"""Smoke test for the core.auth package public surface."""

import pytest


@pytest.mark.unit
def test_core_auth_package_exports() -> None:
    """Every domain type resolves through ``synthorg.core.auth`` without errors.

    Catches accidental circular imports or missing re-exports introduced
    by future edits to the package's ``__init__``.
    """
    from synthorg.core.auth import (
        ApiKey,
        AuthConfig,
        AuthenticatedUser,
        AuthMethod,
        HumanRole,
        OrgRole,
        RefreshConsumeOutcome,
        RefreshRecord,
        RefreshRejectReason,
        Session,
        User,
    )

    assert HumanRole.CEO.value == "ceo"
    assert AuthMethod.JWT.value == "jwt"
    assert OrgRole.OWNER.value == "owner"
    assert AuthConfig.model_config["frozen"] is True
    for cls in (ApiKey, AuthenticatedUser, RefreshRecord, Session, User):
        assert cls.model_config["frozen"] is True
    assert RefreshConsumeOutcome.model_config["frozen"] is True
    assert RefreshRejectReason.SESSION_REVOKED.value == "session_revoked"


@pytest.mark.unit
def test_humanrole_defined_only_in_core_auth_roles() -> None:
    """``HumanRole`` lives in ``core.auth.roles`` and the guards
    module reuses it without redefining a duplicate enum.
    """
    import synthorg.api.guards as guards_module
    from synthorg.core.auth.roles import HumanRole

    assert HumanRole.CEO.value == "ceo"
    assert HumanRole.SYSTEM.value == "system"
    assert guards_module.HumanRole is HumanRole
