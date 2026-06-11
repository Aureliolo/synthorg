"""Tests for atomic org mutations after _org_lock removal.

Verifies that OrgMutationService uses compare-and-swap (CAS) via
``expected_updated_at`` on settings writes, with retry-once-then-raise
semantics.  The module-level ``_org_lock`` is gone; the settings
repository's CAS mechanism serialises concurrent writers.
"""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration  # noqa: F401
from synthorg.api.dto_org import (
    CreateAgentOrgRequest,
    CreateDepartmentRequest,
    ReorderAgentsRequest,
    ReorderDepartmentsRequest,
    UpdateAgentOrgRequest,
    UpdateCompanyRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.hr.seniority import SeniorityLevel
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.fakes import FakePersistenceBackend

# Hardcoded valid Fernet key for settings encryption.
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


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


# ── Module-level lock must not exist ──────────────────────────


@pytest.mark.unit
class TestNoModuleLevelLock:
    """Verify the asyncio.Lock was actually removed."""

    def test_org_lock_removed(self) -> None:
        from synthorg.api.services import org_mutations

        assert not hasattr(org_mutations, "_org_lock"), (
            "_org_lock still exists -- it should be removed"
        )


# ── CAS on department writes ─────────────────────────────────


@pytest.mark.unit
class TestDepartmentCAS:
    """Department mutations use CAS to prevent lost updates."""

    async def test_concurrent_department_create_same_name(
        self,
        service: OrgMutationService,
    ) -> None:
        """Two concurrent creates with the same name.

        One succeeds, the other gets ConflictError (duplicate name)
        or VersionConflictError (CAS failure on retry).
        """
        results: list[str] = []

        async def try_create(idx: int) -> None:
            try:
                await service.create_department(
                    CreateDepartmentRequest(
                        name="Engineering",
                        head=f"head-{idx}",
                        budget_percent=10.0,
                    ),
                )
                results.append("ok")
            except ConflictError:
                results.append("conflict")
            except VersionConflictError:
                results.append("version_conflict")

        async with asyncio.TaskGroup() as tg:
            for i in range(5):
                _ = tg.create_task(try_create(i))

        assert results.count("ok") == 1, f"Expected exactly 1 success, got {results}"

    async def test_concurrent_department_create_different_names(
        self,
        service: OrgMutationService,
    ) -> None:
        """Two concurrent creates with different names.

        Both should eventually succeed (one may retry on CAS failure
        but the retry should succeed since the names don't conflict).
        """
        results: list[str] = []

        async def try_create(name: str) -> None:
            try:
                await service.create_department(
                    CreateDepartmentRequest(
                        name=name,
                        head="head",
                        budget_percent=10.0,
                    ),
                )
                results.append("ok")
            except (ConflictError, VersionConflictError) as exc:
                results.append(f"error:{exc!r}")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(try_create("Engineering"))
            _ = tg.create_task(try_create("Marketing"))

        # Both should succeed (possibly after retry)
        assert results.count("ok") == 2, f"Expected 2 successes, got {results}"


# ── CAS on company updates ───────────────────────────────────


@pytest.mark.unit
class TestCompanyUpdateCAS:
    """Company updates use CAS to prevent lost updates."""

    async def test_concurrent_company_updates_same_etag(
        self,
        service: OrgMutationService,
    ) -> None:
        """Two concurrent updates with the same ETag.

        One succeeds, the other gets VersionConflictError because
        the ETag is stale after the first write.
        """
        # First, set the company name so we have a baseline
        await service.update_company(
            UpdateCompanyRequest(company_name="Original"),
        )

        # Get the current ETag
        etag = await service._company_snapshot_etag()

        results: list[str] = []

        async def try_update(name: str) -> None:
            try:
                await service.update_company(
                    UpdateCompanyRequest(company_name=name),
                    if_match=etag,
                )
                results.append("ok")
            except VersionConflictError:
                results.append("version_conflict")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(try_update("Company-A"))
            _ = tg.create_task(try_update("Company-B"))

        assert results.count("ok") == 1, f"Expected exactly 1 success, got {results}"
        assert results.count("version_conflict") == 1


# ── CAS retry behaviour ──────────────────────────────────────


@pytest.mark.unit
class TestCASRetry:
    """Retry-once-then-raise on VersionConflictError."""

    async def test_retry_succeeds_on_transient_conflict(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a CAS conflict happens on the first attempt, the
        retry reads fresh data and succeeds.
        """
        # Seed a department
        await service.create_department(
            CreateDepartmentRequest(
                name="Engineering",
                head="alice",
                budget_percent=20.0,
            ),
        )

        # Simulate: between read and write, another writer modifies
        # the same key.  We do this by manually updating the settings
        # timestamp right after the service reads.
        from synthorg.core.types import NotBlankStr

        original_get = persistence.settings.get

        call_count = 0

        async def intercepting_get(
            entity_id: tuple[NotBlankStr, NotBlankStr],
        ) -> SettingRow | None:
            nonlocal call_count
            result = await original_get(entity_id)
            namespace, key = entity_id
            if namespace == "company" and key == "departments":
                call_count += 1
                if call_count == 1 and result is not None:
                    # Simulate a concurrent write by changing the timestamp
                    await persistence.settings.save(
                        result.model_copy(
                            update={"updated_at": "2099-01-01T00:00:00+00:00"}
                        )
                    )
            return result

        monkeypatch.setattr(persistence.settings, "get", intercepting_get)
        dept = await service.update_department(
            "Engineering",
            UpdateDepartmentRequest(head="bob"),
        )
        assert dept.head == "bob"
        assert call_count >= 2, f"Expected retry (>= 2 reads), got {call_count}"

    async def test_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When CAS fails on both attempts, VersionConflictError is raised."""
        # Seed a department
        await service.create_department(
            CreateDepartmentRequest(
                name="Engineering",
                head="alice",
                budget_percent=20.0,
            ),
        )

        original_set_if_unchanged = persistence.settings.set_if_unchanged

        async def always_conflict_set_if_unchanged(
            entity: SettingRow,
            expected_updated_at: str | None = None,
        ) -> bool:
            if entity.namespace == "company" and entity.key == "departments":
                return False  # Always CAS failure
            return await original_set_if_unchanged(
                entity,
                expected_updated_at=expected_updated_at,
            )

        monkeypatch.setattr(
            persistence.settings,
            "set_if_unchanged",
            always_conflict_set_if_unchanged,
        )
        with pytest.raises(VersionConflictError):
            await service.update_department(
                "Engineering",
                UpdateDepartmentRequest(head="bob"),
            )


# ── CAS retry coverage for the remaining 6 mutations ──────────


def _always_conflict_for_key(
    monkeypatch: pytest.MonkeyPatch,
    persistence: FakePersistenceBackend,
    target_key: str,
) -> None:
    """Force CAS conflicts on *target_key* until test teardown.

    Used to verify that ``VersionConflictError`` propagates after retries
    are exhausted for every CAS-protected mutation.  Both ``set`` and
    ``set_many`` are intercepted so mutations that bundle multiple keys
    into a single transaction (like ``delete_department``) also surface
    the conflict.  ``monkeypatch`` reverts both attributes when the test
    function returns.
    """
    original_set_if_unchanged = persistence.settings.set_if_unchanged
    original_set_many = persistence.settings.set_many

    async def always_conflict_set_if_unchanged(
        entity: SettingRow,
        expected_updated_at: str | None = None,
    ) -> bool:
        if entity.namespace == "company" and entity.key == target_key:
            return False
        return await original_set_if_unchanged(
            entity,
            expected_updated_at=expected_updated_at,
        )

    async def always_conflict_set_many(
        items: Sequence[SettingRow],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str] | None = None,
    ) -> bool:
        if expected_updated_at_map is not None and any(
            ns == "company" and k == target_key for ns, k in expected_updated_at_map
        ):
            return False
        return await original_set_many(
            items,
            expected_updated_at_map=expected_updated_at_map,
        )

    monkeypatch.setattr(
        persistence.settings,
        "set_if_unchanged",
        always_conflict_set_if_unchanged,
    )
    monkeypatch.setattr(
        persistence.settings,
        "set_many",
        always_conflict_set_many,
    )


async def _seed_dept(service: OrgMutationService, name: str) -> None:
    await service.create_department(
        CreateDepartmentRequest(
            name=name,
            head="alice",
            budget_percent=10.0,
        ),
    )


async def _seed_agent(
    service: OrgMutationService,
    name: str,
    department: str,
) -> None:
    await service.create_agent(
        CreateAgentOrgRequest(
            name=name,
            role="Developer",
            department=department,
            level=SeniorityLevel.MID,
        ),
    )


@pytest.mark.unit
class TestCASRetryCoverage:
    """Every mutation with a CAS retry loop must raise on exhaustion."""

    async def test_delete_department_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        _always_conflict_for_key(monkeypatch, persistence, "departments")
        with pytest.raises(VersionConflictError):
            await service.delete_department("Engineering")

    async def test_reorder_departments_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        await _seed_dept(service, "Marketing")
        _always_conflict_for_key(monkeypatch, persistence, "departments")
        with pytest.raises(VersionConflictError):
            await service.reorder_departments(
                ReorderDepartmentsRequest(
                    department_names=("Marketing", "Engineering"),
                ),
            )

    async def test_create_agent_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        # Seed an initial agent so the `company/agents` setting exists and
        # CAS has a version to compare against on subsequent writes.
        await _seed_agent(service, "alice-dev", "Engineering")
        _always_conflict_for_key(monkeypatch, persistence, "agents")
        with pytest.raises(VersionConflictError):
            await service.create_agent(
                CreateAgentOrgRequest(
                    name="bob-dev",
                    role="Developer",
                    department="Engineering",
                    level=SeniorityLevel.MID,
                ),
            )

    async def test_update_agent_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        await _seed_agent(service, "alice-dev", "Engineering")
        _always_conflict_for_key(monkeypatch, persistence, "agents")
        with pytest.raises(VersionConflictError):
            await service.update_agent(
                "alice-dev",
                UpdateAgentOrgRequest(role="Senior Developer"),
            )

    async def test_delete_agent_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        await _seed_agent(service, "alice-dev", "Engineering")
        _always_conflict_for_key(monkeypatch, persistence, "agents")
        with pytest.raises(VersionConflictError):
            await service.delete_agent("alice-dev")

    async def test_reorder_agents_raises_after_retry_exhausted(
        self,
        service: OrgMutationService,
        persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_dept(service, "Engineering")
        await _seed_agent(service, "alice-dev", "Engineering")
        await _seed_agent(service, "bob-dev", "Engineering")
        _always_conflict_for_key(monkeypatch, persistence, "agents")
        with pytest.raises(VersionConflictError):
            await service.reorder_agents(
                "Engineering",
                ReorderAgentsRequest(agent_names=("bob-dev", "alice-dev")),
            )
