"""Unit tests for on-startup organization read-service wiring.

Exercises ``wire_organization_read_services`` and its per-service branches:
each wire is gated on its dependency (settings for ``TeamService``; the
config resolver + org-mutation service for ``CompanyReadService``; a
connected persistence backend for ``RoleVersionService``) and is idempotent
(an already-wired service is never replaced).
"""

import pytest

from synthorg.api.lifecycle_helpers.organization_wiring import (
    wire_organization_read_services,
)
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.state import AppState
from synthorg.organization._team_service import TeamService
from synthorg.organization.state import OrganizationStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.version_protocol import VersionRepository
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeSettingsService, make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    with_settings: bool = True,
    with_resolver: bool = True,
    with_mutation: bool = True,
) -> AppState:
    """Compose an app state with the requested org-wiring dependencies present.

    ``make_app_state`` skips ``None`` values, so a disabled dependency leaves
    its slice field unset.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(
        settings_service=FakeSettingsService() if with_settings else None,
        config_resolver=mock_of[ConfigResolver]() if with_resolver else None,
        org_mutation_service=mock_of[OrgMutationService]() if with_mutation else None,
    )


class TestTeamServiceWiring:
    async def test_wired_when_settings_present(self) -> None:
        app_state = _app_state()
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).team_service is not None

    async def test_absent_without_settings(self) -> None:
        app_state = _app_state(with_settings=False)
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).team_service is None

    async def test_idempotent_keeps_existing_instance(self) -> None:
        app_state = _app_state()
        existing = TeamService(app_state=app_state)
        app_state.wire(OrganizationStateSlice, team_service=existing)
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).team_service is existing


class TestCompanyReadServiceWiring:
    async def test_wired_with_resolver_and_mutation(self) -> None:
        app_state = _app_state()
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).company_read_service is not None

    async def test_absent_without_resolver(self) -> None:
        app_state = _app_state(with_resolver=False)
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).company_read_service is None

    async def test_absent_without_mutation(self) -> None:
        app_state = _app_state(with_mutation=False)
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).company_read_service is None


class TestRoleVersionServiceWiring:
    async def test_absent_without_persistence(self) -> None:
        app_state = _app_state()
        await wire_organization_read_services(app_state, persistence=None)
        assert app_state.slice(OrganizationStateSlice).role_version_service is None

    async def test_absent_when_persistence_disconnected(self) -> None:
        app_state = _app_state()
        persistence = mock_of[PersistenceBackend](is_connected=False)
        await wire_organization_read_services(app_state, persistence=persistence)
        assert app_state.slice(OrganizationStateSlice).role_version_service is None

    async def test_wired_when_persistence_connected(self) -> None:
        app_state = _app_state()
        persistence = mock_of[PersistenceBackend](
            is_connected=True,
            company_versions=mock_of[VersionRepository](),
            role_versions=mock_of[VersionRepository](),
        )
        await wire_organization_read_services(app_state, persistence=persistence)
        assert app_state.slice(OrganizationStateSlice).role_version_service is not None
