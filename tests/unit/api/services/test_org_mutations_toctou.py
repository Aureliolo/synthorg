"""TOCTOU regression tests for OrgMutationService.delete_department.

PR #1239 left a gap in ``delete_department``: between the
``reference check on agents`` and the ``write on departments``,
a concurrent ``create_agent`` could bind a new agent to the
department being deleted.  The follow-up switches the delete to
``SettingsService.set_many`` with CAS on BOTH departments AND
agents, so any concurrent agents mutation rolls back the delete.
"""

from typing import Any, cast

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.dto_org import CreateDepartmentRequest
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import VersionConflictError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakePersistenceBackend

_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


@pytest.fixture
async def persistence() -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    await backend.connect()
    return backend


@pytest.fixture
def config() -> RootConfig:
    return RootConfig(company_name="test-company")


@pytest.fixture
def settings_service(
    persistence: FakePersistenceBackend,
    config: RootConfig,
) -> SettingsService:
    return SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )


@pytest.fixture
def service(
    settings_service: SettingsService,
    config: RootConfig,
) -> OrgMutationService:
    from synthorg.settings.resolver import ConfigResolver

    resolver = ConfigResolver(
        settings_service=settings_service,
        config=config,
    )
    return OrgMutationService(
        settings_service=settings_service,
        config_resolver=resolver,
    )


async def _seed_dept(service: OrgMutationService, name: str) -> None:
    await service.create_department(
        CreateDepartmentRequest(
            name=name,
            head="alice",
            budget_percent=10.0,
        ),
    )


@pytest.mark.unit
class TestDeleteDepartmentTOCTOU:
    """delete_department pins the agents version to close the TOCTOU."""

    async def test_persistent_agents_conflict_aborts_delete(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
    ) -> None:
        """A persistent CAS miss on the agents key aborts the delete.

        The delete uses set_many with CAS on both departments and
        agents.  When the agents version always fails the CAS check
        (simulating a concurrent writer that keeps bumping it), every
        retry attempt rolls back -- after the retry budget is
        exhausted the delete raises VersionConflictError and the
        department is still present.
        """
        await _seed_dept(service, "Engineering")

        original_set_many = persistence.settings.set_many
        attempts: list[int] = []

        async def always_agents_conflict(
            items: Any,
            *,
            expected_updated_at_map: Any = None,
        ) -> bool:
            if expected_updated_at_map is not None and any(
                ns == "company" and k == "agents" for ns, k in expected_updated_at_map
            ):
                attempts.append(len(attempts) + 1)
                return False
            return cast(
                "bool",
                await original_set_many(
                    items,
                    expected_updated_at_map=expected_updated_at_map,
                ),
            )

        persistence.settings.set_many = always_agents_conflict
        try:
            with pytest.raises(VersionConflictError):
                await service.delete_department("Engineering")
        finally:
            persistence.settings.set_many = original_set_many

        # Department still present: every attempt rolled back.
        deps = await service._read_departments()
        assert any(d.name == "Engineering" for d in deps)
        # The retry loop exhausted at least 2 attempts.
        assert len(attempts) >= 2

    async def test_delete_department_pins_both_keys_in_cas_map(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
    ) -> None:
        """delete passes BOTH departments and agents in the CAS map."""
        await _seed_dept(service, "Engineering")

        captured: list[dict[tuple[str, str], str]] = []
        original_set_many = persistence.settings.set_many

        async def capturing_set_many(
            items: Any,
            *,
            expected_updated_at_map: Any = None,
        ) -> bool:
            if expected_updated_at_map is not None:
                captured.append(dict(expected_updated_at_map))
            return cast(
                "bool",
                await original_set_many(
                    items,
                    expected_updated_at_map=expected_updated_at_map,
                ),
            )

        persistence.settings.set_many = capturing_set_many
        try:
            await service.delete_department("Engineering")
        finally:
            persistence.settings.set_many = original_set_many

        assert captured, "delete_department must go through set_many"
        cas_keys = captured[-1]
        assert ("company", "departments") in cas_keys
        assert ("company", "agents") in cas_keys
