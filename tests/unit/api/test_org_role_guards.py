"""Tests for the require_org_mutation guard factory."""

from typing import Any

import pytest
from litestar import Request
from litestar.exceptions import PermissionDeniedException
from litestar.testing import RequestFactory

from synthorg.api.guards import require_org_mutation
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod, OrgRole
from synthorg.core.auth.roles import HumanRole

_FACTORY = RequestFactory()


def _make_connection(
    *,
    role: HumanRole = HumanRole.CEO,
    org_roles: tuple[OrgRole, ...] = (),
    scoped_departments: tuple[str, ...] = (),
    path: str = "/api/v1/departments/eng",
    path_params: dict[str, str] | None = None,
) -> Request[Any, Any, Any]:
    """Build a real ASGIConnection (litestar ``Request``) for guard testing."""
    user = AuthenticatedUser(
        user_id="test-user",
        username="tester",
        role=role,
        auth_method=AuthMethod.JWT,
        org_roles=org_roles,
        scoped_departments=scoped_departments,
    )
    request = _FACTORY.get(path=path, path_params=path_params or {})
    request.scope["user"] = user
    return request


@pytest.mark.unit
class TestRequireOrgMutationOwnerEditor:
    """Owner and editor roles always pass."""

    def test_owner_always_passes(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(org_roles=(OrgRole.OWNER,))
        guard(conn, None)  # no exception

    def test_editor_always_passes(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(org_roles=(OrgRole.EDITOR,))
        guard(conn, None)

    def test_owner_on_company_level_passes(self) -> None:
        guard = require_org_mutation(department_param=None)
        conn = _make_connection(org_roles=(OrgRole.OWNER,))
        guard(conn, None)

    def test_editor_on_company_level_passes(self) -> None:
        guard = require_org_mutation(department_param=None)
        conn = _make_connection(org_roles=(OrgRole.EDITOR,))
        guard(conn, None)


@pytest.mark.unit
class TestRequireOrgMutationDepartmentAdmin:
    """Department admin scoped-department checks."""

    def test_matching_department_passes(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(
            org_roles=(OrgRole.DEPARTMENT_ADMIN,),
            scoped_departments=("eng", "sales"),
            path_params={"name": "eng"},
        )
        guard(conn, None)

    def test_matching_department_case_insensitive(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(
            org_roles=(OrgRole.DEPARTMENT_ADMIN,),
            scoped_departments=("Engineering",),
            path_params={"name": "engineering"},
        )
        guard(conn, None)

    def test_non_matching_department_rejected(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(
            org_roles=(OrgRole.DEPARTMENT_ADMIN,),
            scoped_departments=("sales",),
            path_params={"name": "eng"},
        )
        with pytest.raises(PermissionDeniedException, match="access denied"):
            guard(conn, None)

    def test_company_level_endpoint_rejected(self) -> None:
        guard = require_org_mutation(department_param=None)
        conn = _make_connection(
            org_roles=(OrgRole.DEPARTMENT_ADMIN,),
            scoped_departments=("eng",),
        )
        with pytest.raises(
            PermissionDeniedException,
            match="company-level",
        ):
            guard(conn, None)


@pytest.mark.unit
class TestRequireOrgMutationViewer:
    """Viewer role is always rejected."""

    def test_viewer_rejected(self) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(
            org_roles=(OrgRole.VIEWER,),
            path_params={"name": "eng"},
        )
        with pytest.raises(
            PermissionDeniedException,
            match="mutation access denied",
        ):
            guard(conn, None)

    def test_viewer_on_company_level_rejected(self) -> None:
        guard = require_org_mutation(department_param=None)
        conn = _make_connection(org_roles=(OrgRole.VIEWER,))
        with pytest.raises(
            PermissionDeniedException,
            match="mutation access denied",
        ):
            guard(conn, None)


@pytest.mark.unit
class TestRequireOrgMutationFallback:
    """Empty org_roles falls back to HumanRole write-access check."""

    @pytest.mark.parametrize(
        ("role", "should_pass"),
        [
            (HumanRole.CEO, True),
            (HumanRole.MANAGER, True),
            (HumanRole.PAIR_PROGRAMMER, True),
            (HumanRole.OBSERVER, False),
            (HumanRole.BOARD_MEMBER, False),
        ],
    )
    def test_fallback_to_human_role(
        self,
        role: HumanRole,
        should_pass: bool,
    ) -> None:
        guard = require_org_mutation(department_param="name")
        conn = _make_connection(role=role, org_roles=())
        if should_pass:
            guard(conn, None)
        else:
            with pytest.raises(
                PermissionDeniedException,
                match="Write access denied",
            ):
                guard(conn, None)
