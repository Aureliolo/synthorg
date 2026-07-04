"""Unit tests for the shared company-departments navigation + CAS write path.

Covers the defensive read branches of ``read_company_departments``, the
compare-and-set retry in ``mutate_company_departments`` (the seam every
``company.departments`` writer routes through), and the tolerant / validating
edges of the settings-backed ``TeamService``.
"""

import json

import pytest

from synthorg.api.state import AppState
from synthorg.core.domain_errors import DomainError, NotFoundError, ValidationError
from synthorg.organization._team_service import TeamService
from synthorg.organization.state import OrganizationStateSlice
from synthorg.organization.team_navigation import (
    mutate_company_departments,
    read_company_departments,
)
from tests._shared import FakeSettingsService, make_app_state

pytestmark = pytest.mark.unit

_DEPT_KEY = ("company", "departments")


def _app_state(departments_value: str | None) -> AppState:
    """Build an app state whose ``company.departments`` holds *value*.

    Passing ``None`` leaves the key unset (the never-written case).

    Returns:
        The composed ``AppState``.
    """
    initial = {} if departments_value is None else {_DEPT_KEY: departments_value}
    return make_app_state(settings_service=FakeSettingsService(initial))


class TestReadCompanyDepartments:
    async def test_absent_setting_returns_empty(self) -> None:
        depts = await read_company_departments(_app_state(None))
        assert depts == []

    async def test_empty_value_returns_empty(self) -> None:
        depts = await read_company_departments(_app_state(""))
        assert depts == []

    async def test_valid_list_parses(self) -> None:
        stored = json.dumps([{"name": "engineering", "teams": []}])
        depts = await read_company_departments(_app_state(stored))
        assert depts == [{"name": "engineering", "teams": []}]

    async def test_corrupt_json_raises_domain_error(self) -> None:
        with pytest.raises(DomainError, match="invalid JSON"):
            await read_company_departments(_app_state("not json{"))

    async def test_non_list_json_raises_domain_error(self) -> None:
        with pytest.raises(DomainError, match="not a list of objects"):
            await read_company_departments(_app_state('{"name": "x"}'))

    async def test_list_of_non_objects_raises_domain_error(self) -> None:
        with pytest.raises(DomainError, match="not a list of objects"):
            await read_company_departments(_app_state('["engineering"]'))


class TestMutateCompanyDepartments:
    async def test_persists_mutation(self) -> None:
        app_state = _app_state(json.dumps([{"name": "engineering", "teams": []}]))

        def _mutate(depts: list[dict[str, object]]) -> int:
            depts.append({"name": "design", "teams": []})
            return len(depts)

        result = await mutate_company_departments(app_state, _mutate)
        assert result == 2
        persisted = await read_company_departments(app_state)
        assert [d["name"] for d in persisted] == ["engineering", "design"]

    async def test_retries_on_version_conflict(self) -> None:
        """A concurrent writer landing mid-flight forces one CAS retry.

        The first ``mutate`` pass bumps the stored version token (simulating
        a rival writer), so the guarded write collides and the handler
        re-reads + re-applies against fresh state rather than clobbering.
        """
        settings = FakeSettingsService(
            {_DEPT_KEY: json.dumps([{"name": "engineering", "teams": []}])}
        )
        app_state = make_app_state(settings_service=settings)
        attempts = {"n": 0}

        def _mutate(depts: list[dict[str, object]]) -> int:
            attempts["n"] += 1
            if attempts["n"] == 1:
                settings.force_version_bump(*_DEPT_KEY)
            depts.append({"name": "design", "teams": []})
            return len(depts)

        result = await mutate_company_departments(app_state, _mutate)
        assert attempts["n"] == 2
        assert result == 2
        persisted = await read_company_departments(app_state)
        assert [d["name"] for d in persisted] == ["engineering", "design"]

    async def test_mutate_error_aborts_without_persisting(self) -> None:
        app_state = _app_state(json.dumps([{"name": "engineering", "teams": []}]))

        def _mutate(depts: list[dict[str, object]]) -> int:
            msg = f"boom ({len(depts)} departments read)"
            raise NotFoundError(msg)

        with pytest.raises(NotFoundError, match="boom"):
            await mutate_company_departments(app_state, _mutate)
        persisted = await read_company_departments(app_state)
        assert [d["name"] for d in persisted] == ["engineering"]


class TestTeamServiceListEdges:
    def _service(self, departments: list[dict[str, object]]) -> TeamService:
        settings = FakeSettingsService({_DEPT_KEY: json.dumps(departments)})
        app_state = make_app_state(
            settings_service=settings,
            slices={OrganizationStateSlice: {}},
        )
        return TeamService(app_state=app_state)

    async def test_rejects_negative_offset(self) -> None:
        svc = self._service([{"name": "engineering", "teams": []}])
        with pytest.raises(ValidationError, match="offset must be >= 0"):
            await svc.list_teams(offset=-1)

    async def test_rejects_non_positive_limit(self) -> None:
        svc = self._service([{"name": "engineering", "teams": []}])
        with pytest.raises(ValidationError, match="limit must be >= 1"):
            await svc.list_teams(limit=0)

    async def test_skips_corrupt_records(self) -> None:
        """One corrupt team/department is skipped, not fatal, so the valid
        teams still list."""
        departments: list[dict[str, object]] = [
            {"name": None, "teams": [{"name": "ghost", "lead": "x"}]},
            {
                "name": "engineering",
                "teams": [
                    {"name": 7, "lead": "x", "members": []},
                    {"name": "backend", "lead": "alice", "members": []},
                ],
            },
        ]
        svc = self._service(departments)
        page, total = await svc.list_teams()
        assert total == 1
        assert [t["name"] for t in page] == ["backend"]
